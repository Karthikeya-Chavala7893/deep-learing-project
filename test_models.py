"""Test all pipeline-compatible models locally"""
from transformers import pipeline
from PIL import Image

img = Image.new('RGB', (224, 224), color=(128, 100, 80))

models = [
    ('Glaucoma', 'pamixsun/swinv2_tiny_for_glaucoma_classification'),
    ('Eye-Disease-All', 'ttangmo24/vit-base-classification-Eye-Diseases'),
    ('DR-ViT', 'Kontawat/vit-diabetic-retinopathy-classification'),
]

for name, model_id in models:
    print(f"\nTesting {name} ({model_id})...")
    try:
        clf = pipeline("image-classification", model=model_id)
        result = clf(img)
        for r in result[:4]:
            print(f"  {r['label']}: {round(r['score'] * 100, 1)}%")
        print(f"  STATUS: OK")
    except Exception as e:
        print(f"  STATUS: FAIL - {str(e)[:150]}")
