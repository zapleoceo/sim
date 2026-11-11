import { useCallback, useReducer, useRef } from 'react';
import { personasApi } from '@/lib/api/personas';
import { errorDetail, errorMessage } from '@/lib/errors';
import type { GuardInfo, PreviewResponse, PreviewTurn } from '@/lib/models';

export interface PreviewMsg {
  role: 'user' | 'assistant';
  text: string;
  meta?: string;
  decision?: unknown;
  guard?: GuardInfo | null;
  /** User message whose reply failed; it is not part of the history until retried successfully. */
  failed?: boolean;
}

export interface PreviewFailure { text: string; history: PreviewTurn[]; error: string }

export interface PreviewState { messages: PreviewMsg[]; busy: boolean; failure: PreviewFailure | null }

export type PreviewAction =
  | { type: 'send'; text: string }
  | { type: 'success'; reply: PreviewResponse }
  | { type: 'fail'; text: string; history: PreviewTurn[]; error: string }
  | { type: 'retry' }
  | { type: 'reset' };

export const INITIAL_PREVIEW: PreviewState = { messages: [], busy: false, failure: null };

/** History sent to the backend: answered turns only (failed user messages are left out). */
export function historyOf(messages: readonly PreviewMsg[]): PreviewTurn[] {
  return messages.filter((m) => !m.failed).map((m) => ({ role: m.role, text: m.text }));
}

export function replyMessage(r: PreviewResponse): PreviewMsg {
  return { role: 'assistant', text: r.reply, meta: `${r.model} · ${r.latency_ms} мс`, decision: r.decision, guard: r.guard };
}

/** Visible error text: the human message plus the backend detail when it is a plain string. */
export function previewError(e: unknown): string {
  const detail = errorDetail(e);
  const base = errorMessage(e);
  return detail ? `${base}: ${detail}` : base;
}

export function previewReducer(state: PreviewState, action: PreviewAction): PreviewState {
  switch (action.type) {
    case 'send':
      return {
        // A new message replaces an unanswered (failed) one.
        messages: [...state.messages.filter((m) => !m.failed), { role: 'user', text: action.text }],
        busy: true,
        failure: null,
      };
    case 'success': {
      const messages = state.messages.map((m) => (m.failed ? { ...m, failed: false } : m));
      return { messages: [...messages, replyMessage(action.reply)], busy: false, failure: null };
    }
    case 'fail': {
      const messages = state.messages.map((m, i) =>
        i === state.messages.length - 1 && m.role === 'user' ? { ...m, failed: true } : m,
      );
      return { messages, busy: false, failure: { text: action.text, history: action.history, error: action.error } };
    }
    case 'retry':
      return { ...state, busy: true, failure: null };
    case 'reset':
      return INITIAL_PREVIEW;
  }
}

/** Preview chat state for one persona: send, retry after an error, reset. */
export function usePreviewChat(personaId: number) {
  const [state, dispatch] = useReducer(previewReducer, INITIAL_PREVIEW);
  const stateRef = useRef(state);
  stateRef.current = state;

  const run = useCallback(
    async (text: string, history: PreviewTurn[]) => {
      try {
        const reply = await personasApi.preview(personaId, text, history);
        dispatch({ type: 'success', reply });
      } catch (e) {
        console.error('[preview]', e);
        dispatch({ type: 'fail', text, history, error: previewError(e) });
      }
    },
    [personaId],
  );

  const send = useCallback(
    (raw: string): boolean => {
      const text = raw.trim();
      const s = stateRef.current;
      if (!text || s.busy) return false;
      const history = historyOf(s.messages);
      dispatch({ type: 'send', text });
      void run(text, history);
      return true;
    },
    [run],
  );

  const retry = useCallback(() => {
    const f = stateRef.current.failure;
    if (!f || stateRef.current.busy) return;
    dispatch({ type: 'retry' });
    void run(f.text, f.history);
  }, [run]);

  const reset = useCallback(() => dispatch({ type: 'reset' }), []);

  return { ...state, send, retry, reset };
}
