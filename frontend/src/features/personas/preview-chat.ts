import { useCallback, useEffect, useReducer, useRef } from 'react';
import { personasApi } from '@/lib/api/personas';
import { errorDetail, errorMessage } from '@/lib/errors';
import {
  forgetPreview, newPreviewKey, pendingPreview, pollPreview, PreviewOperationError,
  rememberPreview, TerminalPreviewError,
} from './preview-operation';
import type { CompletedPreview, PreviewInput } from './preview-operation';
import type { GuardInfo, PreviewAttachment, PreviewContext, PreviewResponse, PreviewStageDiagnostics, PreviewStageOptions, PreviewTurn, PreviewVoice } from '@/lib/models';

export interface PreviewMsg {
  role: 'user' | 'assistant';
  /** What the model reads: typed text, a transcript or a file description. */
  text: string;
  meta?: string;
  decision?: unknown;
  guard?: GuardInfo | null;
  stages?: PreviewStageDiagnostics;
  /** The client's voice note or file, shown above the text. */
  attachment?: PreviewAttachment;
  /** The persona's voice note. */
  voice?: PreviewVoice | null;
  /** User message whose reply failed; it is not part of the history until retried successfully. */
  failed?: boolean;
}

export interface PreviewFailure { text: string; history: PreviewTurn[]; error: string; voiceInput?: boolean; terminal?: boolean }

export interface PreviewState {
  messages: PreviewMsg[];
  context: PreviewContext;
  stages: PreviewStageOptions;
  busy: boolean;
  /** A voice note or file is being recognized by the Broker. */
  uploading: boolean;
  failure: PreviewFailure | null;
  uploadError: string | null;
}

export type PreviewAction =
  | { type: 'send'; text: string; attachment?: PreviewAttachment }
  | { type: 'success'; reply: PreviewResponse }
  | { type: 'restore'; completed: CompletedPreview }
  | { type: 'fail'; text: string; history: PreviewTurn[]; error: string; voiceInput?: boolean; terminal?: boolean }
  | { type: 'retry' }
  | { type: 'upload' }
  | { type: 'upload-fail'; error: string }
  | { type: 'stages'; stages: PreviewStageOptions }
  | { type: 'reset' };

export const PROMPT_ONLY_STAGES: PreviewStageOptions = { schedule: false, debounce: false, decision_context: false };

export const INITIAL_PREVIEW: PreviewState = {
  messages: [], context: { stage: "first_contact", status: "active", needs: [], next_step: "" },
  stages: { ...PROMPT_ONLY_STAGES, decision_context: true },
  busy: false, uploading: false, failure: null, uploadError: null,
};

/** History sent to the backend: answered turns only (failed user messages are left out). */
export function historyOf(messages: readonly PreviewMsg[]): PreviewTurn[] {
  return messages.filter((m) => !m.failed).map((m) => ({ role: m.role, text: m.text }));
}

export function replyMessage(r: PreviewResponse): PreviewMsg {
  return {
    role: 'assistant', text: r.reply, meta: `${r.model} · ${r.latency_ms} мс`, decision: r.decision, guard: r.guard,
    voice: r.voice ?? null, stages: r.stages,
  };
}

/** The message the model reads for an attachment, with an optional typed caption first. */
export function attachmentText(caption: string, a: PreviewAttachment): string {
  const c = caption.trim();
  return c ? `${c}\n${a.text}` : a.text;
}

/** Visible error text: the human message plus the backend detail when it is a plain string. */
export function previewError(e: unknown): string {
  const detail = errorDetail(e);
  const base = errorMessage(e);
  return detail ? `${base}: ${detail}` : base;
}

export function previewReducer(state: PreviewState, action: PreviewAction): PreviewState {
  switch (action.type) {
    case 'send': {
      const msg: PreviewMsg = { role: 'user', text: action.text, ...(action.attachment ? { attachment: action.attachment } : {}) };
      return {
        // A new message replaces an unanswered (failed) one.
        messages: [...state.messages.filter((m) => !m.failed), msg],
        context: state.context,
        stages: state.stages,
        busy: true,
        uploading: false,
        failure: null,
        uploadError: null,
      };
    }
    case 'success': {
      const messages = state.messages.map((m) => (m.failed ? { ...m, failed: false } : m));
      return { ...state, messages: [...messages, replyMessage(action.reply)],
        context: action.reply.context ?? state.context, busy: false, failure: null };
    }
    case 'restore': {
      const { result, history, message } = action.completed;
      return { ...state, busy: false, failure: null, context: result.context ?? state.context,
        messages: [...history, { role: 'user', text: message }, replyMessage(result)] };
    }
    case 'fail': {
      const messages = state.messages.map((m, i) =>
        i === state.messages.length - 1 && m.role === 'user' ? { ...m, failed: true } : m,
      );
      const failure: PreviewFailure = { text: action.text, history: action.history, error: action.error };
      if (action.voiceInput) failure.voiceInput = true;
      if (action.terminal) failure.terminal = true;
      return { ...state, messages, busy: false, failure };
    }
    case 'retry':
      return { ...state, busy: true, failure: null };
    case 'upload':
      return { ...state, uploading: true, uploadError: null };
    case 'upload-fail':
      return { ...state, uploading: false, uploadError: action.error };
    case 'stages':
      return isIdle(state) ? { ...state, stages: action.stages } : state;
    case 'reset':
      return isIdle(state) ? INITIAL_PREVIEW : state;
  }
}

/** Nothing is being sent or recognized right now. */
export function isIdle(s: PreviewState): boolean {
  return !s.busy && !s.uploading;
}

/** Preview chat state for one persona: send text, a voice note or a file; retry; reset. */
export function usePreviewChat(personaId: number, asyncEnabled: boolean, ownerId: number) {
  const [state, dispatch] = useReducer(previewReducer, INITIAL_PREVIEW);
  const stateRef = useRef(state);
  stateRef.current = state;

  const operation = useRef<{ key: string; input?: PreviewInput } | null>(null);
  const activePoll = useRef<AbortController | null>(null);

  const check = useCallback(async (key: string, input?: PreviewInput, restoring = false) => {
    activePoll.current?.abort();
    const controller = new AbortController();
    activePoll.current = controller;
    const { signal } = controller;
    try {
      const complete = await pollPreview(personaId, key, signal, input);
      if (signal.aborted) return;
      forgetPreview(personaId, key, ownerId);
      operation.current = null;
      dispatch(restoring ? { type: 'restore', completed: complete } : { type: 'success', reply: complete.result });
    } catch (e) {
      if (signal.aborted) return;
      const terminal = e instanceof TerminalPreviewError;
      if (terminal) {
        forgetPreview(personaId, key, ownerId);
        operation.current = null;
      }
      dispatch({
        type: 'fail', text: input?.message ?? '', history: input?.history ?? [],
        error: e instanceof PreviewOperationError ? e.message : previewError(e),
        voiceInput: input?.voice_input, terminal,
      });
    } finally {
      if (activePoll.current === controller) activePoll.current = null;
    }
  }, [ownerId, personaId]);

  useEffect(() => {
    const key = pendingPreview(personaId, ownerId);
    if (key) {
      operation.current = { key };
      dispatch({ type: 'retry' });
      void check(key, undefined, true);
    }
    return () => activePoll.current?.abort();
  }, [check, ownerId, personaId]);

  const run = useCallback(
    async (text: string, history: PreviewTurn[], voiceInput: boolean) => {
      const { context, stages } = stateRef.current;
      try {
        if (asyncEnabled) {
          const key = newPreviewKey();
          const input = { ...context, message: text, history, voice_input: voiceInput, stages };
          rememberPreview(personaId, key, ownerId);
          operation.current = { key, input };
          await check(key, input);
          return;
        }
        const reply = await personasApi.preview(personaId, text, history, voiceInput, context, stages);
        dispatch({ type: 'success', reply });
      } catch (e) {
        dispatch({ type: 'fail', text, history, error: previewError(e), voiceInput });
      }
    },
    [asyncEnabled, check, ownerId, personaId],
  );

  const send = useCallback(
    (raw: string): boolean => {
      const text = raw.trim();
      if (!text || activePoll.current || operation.current || !isIdle(stateRef.current)) return false;
      const history = historyOf(stateRef.current.messages);
      dispatch({ type: 'send', text });
      void run(text, history, false);
      return true;
    },
    [run],
  );

  /** A recorded voice note (`voice`) or an attached file: recognize it, then send its text. */
  const sendFile = useCallback(
    async (file: Blob, filename: string, opts: { voice?: boolean; caption?: string } = {}): Promise<boolean> => {
      if (activePoll.current || operation.current || !isIdle(stateRef.current)) return false;
      dispatch({ type: 'upload' });
      let attachment: PreviewAttachment;
      try {
        attachment = await personasApi.attachment(personaId, file, filename, opts.voice ?? false);
      } catch (e) {
        console.error('[preview attachment]', e);
        dispatch({ type: 'upload-fail', error: previewError(e) });
        return false;
      }
      const text = attachmentText(opts.caption ?? '', attachment);
      const history = historyOf(stateRef.current.messages);
      dispatch({ type: 'send', text, attachment });
      void run(text, history, attachment.kind === 'voice');
      return true;
    },
    [personaId, run],
  );

  const retry = useCallback(() => {
    const f = stateRef.current.failure;
    if (!f || f.terminal || stateRef.current.busy) return;
    dispatch({ type: 'retry' });
    const current = operation.current;
    if (current) void check(current.key, undefined, !current.input);
    else void run(f.text, f.history, f.voiceInput ?? false);
  }, [check, run]);

  const reset = useCallback(() => {
    if (!isIdle(stateRef.current) || operation.current) return;
    dispatch({ type: 'reset' });
  }, []);

  const setStages = useCallback((stages: PreviewStageOptions) => dispatch({ type: 'stages', stages }), []);

  return { ...state, send, sendFile, retry, reset, setStages, queued: asyncEnabled || operation.current !== null, awaitingOperation: operation.current !== null };
}
