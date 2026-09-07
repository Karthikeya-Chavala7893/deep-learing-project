"""
backend/triage.py
─────────────────
Rule-based triage engine for **Daily Home Mode**.

Single Responsibility
─────────────────────
Turn a set of self-reported symptoms — optionally augmented by two cheap colour
cues read off a smartphone photo — into the same ``[{label, confidence}, ...]``
shape ``model.predict()`` emits, so the whole frontend result pipeline is
reused unchanged.

Design constraints (mirroring ``model.py`` §4.3):
  * MUST NOT import flask, firebase_admin, firestore or any HTTP library.
  * MUST NOT import torch, timm or transformers — Home Mode carries **zero**
    ML cost. One screening is a few hundred float multiplications.
  * MUST NOT write to disk — the optional photo stays in ``io.BytesIO``.
  * Deterministic: identical input always yields identical output, which makes
    the module unit-testable without fixtures or mocks.
"""

import io
import logging

from PIL import Image

logger = logging.getLogger('visionai.triage')

# ── Canonical home-card keys (mirrored in frontend/src/lib/diseases.ts) ──────
CARD_ALLERGY = 'Home_Allergy_Irritation'
CARD_DIGITAL_STRAIN = 'Home_Digital_Strain'
CARD_RED_EYE = 'Home_Red_Eye'
CARD_LENS_HAZE = 'Home_Lens_Haze'
CARD_VISION_ALERT = 'Home_Vision_Loss_Alert'

#: Every card the engine can surface, in escalating severity order.
CARDS: tuple[str, ...] = (
    CARD_ALLERGY,
    CARD_DIGITAL_STRAIN,
    CARD_RED_EYE,
    CARD_LENS_HAZE,
    CARD_VISION_ALERT,
)

# ═════════════════════════════════════════════════════════════════════════════
# SYMPTOM CATALOGUE
# ═════════════════════════════════════════════════════════════════════════════
#
# Each symptom contributes weight to one primary card and, where the clinical
# picture genuinely overlaps, a smaller weight to neighbouring cards. The ids
# are the wire contract with the frontend checklist — never rename one without
# updating `frontend/src/lib/homeTriage.ts` in the same commit.

SYMPTOM_WEIGHTS: dict[str, dict[str, float]] = {
    # ── Card 1: allergic surface irritation ─────────────────────────────────
    'itching':                  {CARD_ALLERGY: 3.0, CARD_RED_EYE: 0.5},
    'watering':                 {CARD_ALLERGY: 2.5, CARD_DIGITAL_STRAIN: 0.5},
    'seasonal_allergies':       {CARD_ALLERGY: 3.0},
    'puffy_lids':               {CARD_ALLERGY: 2.0, CARD_RED_EYE: 1.0},
    'gritty_feeling':           {CARD_ALLERGY: 2.0, CARD_DIGITAL_STRAIN: 1.5},

    # ── Card 2: digital eye strain & dry eye ────────────────────────────────
    'long_screen_hours':        {CARD_DIGITAL_STRAIN: 3.0},
    'evening_burning':          {CARD_DIGITAL_STRAIN: 2.5, CARD_ALLERGY: 0.5},
    'dryness':                  {CARD_DIGITAL_STRAIN: 2.5, CARD_ALLERGY: 0.5},
    'headache_after_screens':   {CARD_DIGITAL_STRAIN: 2.5},
    'blur_clears_on_rest':      {CARD_DIGITAL_STRAIN: 3.0},

    # ── Card 3: bloodshot red eye & conjunctivitis ──────────────────────────
    'redness':                  {CARD_RED_EYE: 3.0, CARD_ALLERGY: 0.5},
    'crusty_discharge':         {CARD_RED_EYE: 3.5},
    'contagious_contact':       {CARD_RED_EYE: 3.0},
    'contact_lens_discomfort':  {CARD_RED_EYE: 2.0, CARD_DIGITAL_STRAIN: 0.5},
    'blood_patch':              {CARD_RED_EYE: 3.0},

    # ── Card 4: visible lens cloudiness & pupil haze ────────────────────────
    'cloudy_pupil':             {CARD_LENS_HAZE: 3.5},
    'faded_colours':            {CARD_LENS_HAZE: 3.0},
    'night_halos':              {CARD_LENS_HAZE: 3.0, CARD_VISION_ALERT: 0.5},
    'age_60_plus':              {CARD_LENS_HAZE: 2.0},
    'double_vision_one_eye':    {CARD_LENS_HAZE: 2.5, CARD_VISION_ALERT: 1.0},

    # ── Card 5: early vision loss red alert ─────────────────────────────────
    'sudden_blur':              {CARD_VISION_ALERT: 4.0},
    'floaters_or_flashes':      {CARD_VISION_ALERT: 4.0},
    'tunnel_vision':            {CARD_VISION_ALERT: 4.0},
    'severe_pain_nausea':       {CARD_VISION_ALERT: 4.0, CARD_RED_EYE: 1.0},
    'diabetes_or_bp':           {CARD_VISION_ALERT: 2.5},
}

#: Symptoms that must never be buried behind a milder card, however many mild
#: boxes the patient also ticked. Any one of these forces Card 5 to rank first.
RED_FLAG_SYMPTOMS: frozenset[str] = frozenset({
    'sudden_blur',
    'floaters_or_flashes',
    'tunnel_vision',
    'severe_pain_nausea',
})

#: Multiplier applied to the red-alert score once a red flag is present.
_RED_FLAG_MULTIPLIER = 2.0

#: Additive floor guaranteeing the red-alert card outranks every other card.
_RED_FLAG_MARGIN = 2.0

#: Maximum number of symptom ids accepted in one request (abuse ceiling).
MAX_SYMPTOMS = 40

#: Percentage match values are rounded to this many decimal places.
_MATCH_DECIMALS = 2

#: Cards scoring below this percentage share are dropped from the response.
_MIN_REPORTED_MATCH = 0.5


# ═════════════════════════════════════════════════════════════════════════════
# IMAGE CUES  (deliberately trivial — no ML, no numpy, no model weights)
# ═════════════════════════════════════════════════════════════════════════════

#: The photo is downsampled to this square before any pixel is inspected, so a
#: 12-megapixel phone shot costs the same as a thumbnail.
_CUE_SAMPLE_SIZE = 64

#: Central crop fraction searched for lens/pupil haze.
_CENTRAL_CROP = 0.4

#: Luminance at or above which a desaturated pixel counts as "milky".
_HAZE_LUMA_FLOOR = 120

#: Saturation at or below which a bright pixel counts as "washed out".
_HAZE_SATURATION_CEILING = 60

#: Divisor normalising mean red dominance (0-255) into a 0-1 cue.
_REDNESS_SCALE = 64.0

#: Each cue is worth at most this many weight points, so a photo can nudge the
#: ranking but never outvote what the patient actually reports.
_CUE_MAX_WEIGHT = 2.5


def inspect_image(image_bytes: bytes) -> dict[str, float]:
    """Extract two coarse colour cues from a smartphone eye photo.

    The photo is downsampled to 64x64 and reduced to two scalars:

      * ``redness`` — how far red dominates green/blue across the frame, the
        signature of a bloodshot sclera.
      * ``haze``    — the share of the central crop that is bright *and*
        desaturated, the signature of a milky lens over the pupil.

    Both are advisory: they feed the same additive score as a ticked checkbox
    and are capped well below the weight of a reported symptom.

    Args:
        image_bytes: Raw bytes of the uploaded photo.

    Returns:
        ``{'redness': 0.0-1.0, 'haze': 0.0-1.0}``. An unreadable or empty image
        yields zeros rather than raising — a bad photo must never fail an
        otherwise valid symptom-driven screening.
    """
    if not image_bytes:
        return {'redness': 0.0, 'haze': 0.0}

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        image = image.resize((_CUE_SAMPLE_SIZE, _CUE_SAMPLE_SIZE))
    except (Image.DecompressionBombError, Image.UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("Home-mode photo could not be inspected, ignoring cues: %s", exc)
        return {'redness': 0.0, 'haze': 0.0}

    pixels = list(image.getdata())
    if not pixels:
        return {'redness': 0.0, 'haze': 0.0}

    # ── Cue 1: global red dominance ─────────────────────────────────────────
    redness_total = 0.0
    for red, green, blue in pixels:
        redness_total += max(0, red - max(green, blue))
    redness = min(1.0, (redness_total / len(pixels)) / _REDNESS_SCALE)

    # ── Cue 2: bright, desaturated centre ───────────────────────────────────
    margin = int(_CUE_SAMPLE_SIZE * (1 - _CENTRAL_CROP) / 2)
    hazy = 0
    central = 0
    for row in range(margin, _CUE_SAMPLE_SIZE - margin):
        for column in range(margin, _CUE_SAMPLE_SIZE - margin):
            red, green, blue = pixels[row * _CUE_SAMPLE_SIZE + column]
            central += 1
            luma = (red + green + blue) / 3
            saturation = max(red, green, blue) - min(red, green, blue)
            if luma >= _HAZE_LUMA_FLOOR and saturation <= _HAZE_SATURATION_CEILING:
                hazy += 1
    haze = (hazy / central) if central else 0.0

    return {'redness': round(redness, 4), 'haze': round(haze, 4)}


def _cue_weights(cues: dict[str, float] | None) -> dict[str, float]:
    """Convert raw image cues into per-card score contributions."""
    if not cues:
        return {}
    return {
        CARD_RED_EYE: float(cues.get('redness', 0.0)) * _CUE_MAX_WEIGHT,
        CARD_LENS_HAZE: float(cues.get('haze', 0.0)) * _CUE_MAX_WEIGHT,
    }


# ═════════════════════════════════════════════════════════════════════════════
# SCORING
# ═════════════════════════════════════════════════════════════════════════════

def known_symptoms() -> list[str]:
    """List every symptom id the engine recognises."""
    return sorted(SYMPTOM_WEIGHTS)


def assess(symptom_ids, cues: dict[str, float] | None = None) -> list[dict]:
    """Score the five home cards against reported symptoms and photo cues.

    Pipeline:
        symptom ids -> additive per-card weights
                    -> optional image-cue weights
                    -> red-flag escalation
                    -> normalise to a percentage match, sorted descending

    Args:
        symptom_ids: Iterable of symptom ids from ``SYMPTOM_WEIGHTS``. Unknown
            ids are ignored rather than rejected, so an older client is never
            broken by a catalogue change.
        cues: Optional output of :func:`inspect_image`.

    Returns:
        Cards sorted by descending match percentage, each
        ``{'label': str, 'confidence': float}``. The first element additionally
        carries ``'red_flag': True`` when an urgent symptom forced the
        escalation, so the UI can surface the hospital shortcut.

    Raises:
        ValueError: If nothing scored — no recognised symptom and no usable
            photo cue — since there is then no defensible card to show.
    """
    selected = [s for s in dict.fromkeys(symptom_ids or []) if s in SYMPTOM_WEIGHTS]

    scores: dict[str, float] = {card: 0.0 for card in CARDS}
    for symptom in selected:
        for card, weight in SYMPTOM_WEIGHTS[symptom].items():
            scores[card] += weight
    for card, weight in _cue_weights(cues).items():
        scores[card] += weight

    red_flag = any(symptom in RED_FLAG_SYMPTOMS for symptom in selected)
    if red_flag:
        others = sum(score for card, score in scores.items() if card != CARD_VISION_ALERT)
        scores[CARD_VISION_ALERT] = max(
            scores[CARD_VISION_ALERT] * _RED_FLAG_MULTIPLIER,
            others + _RED_FLAG_MARGIN,
        )

    total = sum(scores.values())
    if total <= 0:
        raise ValueError(
            "No recognised symptoms were selected and the photo showed no usable cues."
        )

    results = [
        {'label': card, 'confidence': round(score / total * 100, _MATCH_DECIMALS)}
        for card, score in scores.items()
        if (score / total * 100) >= _MIN_REPORTED_MATCH
    ]
    results.sort(key=lambda item: item['confidence'], reverse=True)

    if red_flag and results:
        results[0]['red_flag'] = True

    return results
