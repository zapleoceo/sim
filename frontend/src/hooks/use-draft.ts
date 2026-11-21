import { useCallback, useState } from 'react';
import { useUnsavedSection } from '@/components/unsaved-changes';
import { sameJson } from '@/lib/utils';

export interface DraftOptions<T> {
  /** Value equality; plain data by default. */
  equals?: (a: T, b: T) => boolean;
  /** The section's name in «Не сохранено: …»; without it the draft has no unsaved-changes guard. */
  guardName?: string;
}

export interface Draft<T> {
  /** What the form shows and edits. */
  draft: T;
  setDraft: (next: T | ((draft: T) => T)) => void;
  /** The server copy the draft started from: a save sends the difference against it. */
  baseline: T;
  dirty: boolean;
  /** «Отменить»: back to the newest server copy. */
  reset: () => void;
  /** After a save: `saved` becomes the baseline; `rebase` keeps other unsaved edits in the draft. */
  commit: (saved: T, rebase?: (draft: T) => T) => void;
}

interface State<T> {
  baseline: T;
  draft: T;
  /** The newest server value seen (newer than the baseline while the draft is being edited). */
  server: T;
}

/**
 * The one draft of every editor (docs/frontend.md, «Черновики»). A fresh server value (a live
 * update, another tab) replaces the form only while it is untouched; while it is edited the
 * baseline stays the copy the edit started from, so a save never sends values the operator did
 * not touch.
 */
export function useDraft<T>(serverValue: T, { equals = sameJson, guardName }: DraftOptions<T> = {}): Draft<T> {
  const [state, setState] = useState<State<T>>(() => ({ baseline: serverValue, draft: serverValue, server: serverValue }));
  let current = state;
  if (!equals(serverValue, state.server)) {
    // Adjusting state while rendering (React's pattern for «a prop changed»): no stale frame.
    current = equals(state.draft, state.baseline)
      ? { baseline: serverValue, draft: serverValue, server: serverValue }
      : { ...state, server: serverValue };
    setState(current);
  }
  const dirty = !equals(current.draft, current.baseline);
  useUnsavedSection(guardName ?? '', Boolean(guardName) && dirty);

  const setDraft = useCallback((next: T | ((draft: T) => T)) => {
    setState((s) => ({ ...s, draft: typeof next === 'function' ? (next as (draft: T) => T)(s.draft) : next }));
  }, []);
  const reset = useCallback(() => setState((s) => ({ ...s, baseline: s.server, draft: s.server })), []);
  const commit = useCallback((saved: T, rebase?: (draft: T) => T) => {
    setState((s) => ({ ...s, baseline: saved, draft: rebase ? rebase(s.draft) : saved }));
  }, []);

  return { draft: current.draft, setDraft, baseline: current.baseline, dirty, reset, commit };
}
