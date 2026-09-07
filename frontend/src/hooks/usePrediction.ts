'use client';

/**
 * hooks/usePrediction.ts
 * ──────────────────────
 * Screening state machine, shared by both arms of the dual-mode gateway.
 *
 *   idle → uploading → predicting → success | error → idle
 *
 * Owns the pending request (clinical image, or home symptoms plus an optional
 * photo), its object-URL preview (revoked on unmount so blobs never leak), the
 * in-flight fetch, and the resulting cards.
 */

import { useCallback, useEffect, useReducer, useRef } from 'react';

import { predictImage, screenHome } from '@/lib/api';
import { validateImageDimensions, validateImageFile } from '@/lib/validation';
import { ApiError } from '@/types/api';
import type { PredictResponse, ScreeningMode } from '@/types/prediction';
import { useAuth } from '@/hooks/useAuth';

/** Where the screening flow currently sits. */
export type PredictionStatus = 'idle' | 'uploading' | 'predicting' | 'success' | 'error';

/**
 * Everything needed to replay a screening on retry.
 *
 * Clinical screenings always carry a file; home screenings carry the ticked
 * symptoms and may carry a photo.
 */
export type PendingRequest =
  | { mode: 'clinical'; file: File }
  | { mode: 'home'; symptoms: string[]; file: File | null };

interface PredictionState {
  status: PredictionStatus;
  /** The last submitted request, kept so `retry` can replay it. */
  pending: PendingRequest | null;
  previewUrl: string | null;
  result: PredictResponse | null;
  error: string | null;
}

type PredictionAction =
  | { type: 'submit'; pending: PendingRequest; previewUrl: string | null }
  | { type: 'predicting' }
  | { type: 'success'; result: PredictResponse }
  | { type: 'error'; error: string }
  | { type: 'reset' };

const INITIAL_STATE: PredictionState = {
  status: 'idle',
  pending: null,
  previewUrl: null,
  result: null,
  error: null,
};

function reducer(state: PredictionState, action: PredictionAction): PredictionState {
  switch (action.type) {
    case 'submit':
      return {
        status: 'uploading',
        pending: action.pending,
        previewUrl: action.previewUrl,
        result: null,
        error: null,
      };
    case 'predicting':
      return { ...state, status: 'predicting', error: null };
    case 'success':
      return { ...state, status: 'success', result: action.result, error: null };
    case 'error':
      return { ...state, status: 'error', error: action.error };
    case 'reset':
      return INITIAL_STATE;
    default:
      return state;
  }
}

export interface UsePredictionResult extends PredictionState {
  /** True while the request is in flight. */
  busy: boolean;
  /** Convenience view of `pending.file` for the clinical uploader. */
  file: File | null;
  /** Validate, preview and classify a chosen retinal image (clinical mode). */
  analyze: (file: File) => Promise<void>;
  /** Score a symptom checklist, with an optional photo (home mode). */
  assess: (symptoms: string[], file?: File | null) => Promise<void>;
  /** Re-run the last submitted screening. */
  retry: () => Promise<void>;
  /** Clear everything and return to the upload prompt. */
  reset: () => void;
}

/**
 * Drive the upload → inference → results flow for either screening mode.
 *
 * @returns Current screening state plus the actions that advance it.
 */
export function usePrediction(): UsePredictionResult {
  const { user } = useAuth();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  const abortRef = useRef<AbortController | null>(null);
  const previewRef = useRef<string | null>(null);

  // Revoke the previous object URL whenever it changes, and on unmount.
  useEffect(() => {
    previewRef.current = state.previewUrl;
  }, [state.previewUrl]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    [],
  );

  const send = useCallback(
    async (pending: PendingRequest): Promise<void> => {
      if (!user) {
        dispatch({ type: 'error', error: 'Your session expired. Please sign in again.' });
        return;
      }

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      dispatch({ type: 'predicting' });
      try {
        const result =
          pending.mode === 'clinical'
            ? await predictImage(user, pending.file, controller.signal)
            : await screenHome(user, pending.symptoms, pending.file, controller.signal);
        dispatch({ type: 'success', result });
      } catch (cause) {
        if (cause instanceof DOMException && cause.name === 'AbortError') return;
        if (cause instanceof ApiError) {
          const message =
            cause.status === 401
              ? 'Your session expired. Please sign in again.'
              : cause.status === 429
                ? 'Too many screenings in a short time. Please wait a minute and try again.'
                : cause.message;
          dispatch({ type: 'error', error: message });
          return;
        }
        dispatch({ type: 'error', error: 'Analysis failed. Please try again.' });
      }
    },
    [user],
  );

  /**
   * Run both client-side gates over a chosen file.
   *
   * @returns True when the file may be submitted; false after dispatching the
   *   rejection reason as the visible error.
   */
  const validateFile = useCallback(async (file: File): Promise<boolean> => {
    const basic = validateImageFile(file);
    if (!basic.ok) {
      dispatch({ type: 'error', error: basic.error ?? 'Invalid file' });
      return false;
    }
    const dimensions = await validateImageDimensions(file);
    if (!dimensions.ok) {
      dispatch({ type: 'error', error: dimensions.error ?? 'Invalid image' });
      return false;
    }
    return true;
  }, []);

  const analyze = useCallback(
    async (file: File): Promise<void> => {
      if (!(await validateFile(file))) return;

      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
      const pending: PendingRequest = { mode: 'clinical', file };
      dispatch({ type: 'submit', pending, previewUrl: URL.createObjectURL(file) });
      await send(pending);
    },
    [validateFile, send],
  );

  const assess = useCallback(
    async (symptoms: string[], file: File | null = null): Promise<void> => {
      if (file && !(await validateFile(file))) return;

      // Home Mode shows the photo in its own intake form, so the hook holds no
      // preview of its own here — one object URL per blob, owned by one place.
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
      const pending: PendingRequest = { mode: 'home', symptoms, file };
      dispatch({ type: 'submit', pending, previewUrl: null });
      await send(pending);
    },
    [validateFile, send],
  );

  const retry = useCallback(async (): Promise<void> => {
    if (state.pending) await send(state.pending);
  }, [send, state.pending]);

  const reset = useCallback((): void => {
    abortRef.current?.abort();
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = null;
    dispatch({ type: 'reset' });
  }, []);

  return {
    ...state,
    busy: state.status === 'uploading' || state.status === 'predicting',
    file: state.pending?.file ?? null,
    analyze,
    assess,
    retry,
    reset,
  };
}

/** Narrow a response to the mode that produced it. */
export function isHomeResult(result: PredictResponse | null): boolean {
  return result?.mode === ('home' satisfies ScreeningMode);
}
