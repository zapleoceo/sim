import { useCallback, useEffect, useRef, useState } from 'react';
import { pollQueued, PreviewOperationError, TerminalPreviewError } from '@/lib/api/queued-operation';
import { errorMessage } from '@/lib/errors';

type CaptionResult = { id: string; status: 'complete'; result: { caption: string } };
type Pending = { post: number; key: string };

function storage(owner: number, post: number): string {
  return `sim.caption.pending.${owner}.${post}`;
}

export function useCaptionQueue(owner: number, post: number | undefined) {
  const active = useRef<AbortController | null>(null);
  const seen = useRef<string | null>(null);
  const scope = useRef({ owner, post });
  const [pending, setPending] = useState<Pending | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async (op: Pending, submit = false) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    seen.current = op.key;
    setPending(op);
    setWaiting(true);
    setError(null);
    try {
      const path = (key: string) => `/api/content/${op.post}/caption-operations/${encodeURIComponent(key)}`;
      const remember = (id: string) => {
        sessionStorage.setItem(storage(owner, op.post), id);
        seen.current = id;
        setPending({ post: op.post, key: id });
        return path(id);
      };
      const item = await pollQueued<CaptionResult>(path(op.key), controller.signal, submit ? {} : undefined, remember);
      if (controller.signal.aborted) return;
      remember(item.id);
      setSuggestion(item.result.caption);
    } catch (e) {
      if (controller.signal.aborted) return;
      if (e instanceof TerminalPreviewError) {
        sessionStorage.removeItem(storage(owner, op.post));
        setPending(null);
      }
      setError(e instanceof PreviewOperationError ? e.message : errorMessage(e));
    } finally {
      if (active.current === controller) {
        active.current = null;
        setWaiting(false);
      }
    }
  }, [owner]);

  useEffect(() => () => {
    active.current?.abort();
    active.current = null;
    seen.current = null;
  }, [owner]);

  useEffect(() => {
    const previous = scope.current;
    scope.current = { owner, post };
    if (owner !== previous.owner || (previous.post !== undefined && previous.post !== post)) {
      active.current?.abort();
      active.current = null;
      seen.current = null;
      setPending(null);
      setWaiting(false);
      setSuggestion(null);
      setError(null);
    }
    if (post === undefined) return;
    const key = sessionStorage.getItem(storage(owner, post));
    if (key && key !== seen.current) void check({ post, key });
  }, [check, owner, post]);

  const start = useCallback(async (postId: number) => {
    const saved = sessionStorage.getItem(storage(owner, postId));
    if (saved) return check({ post: postId, key: saved });
    const key = `${Date.now()}-${crypto.randomUUID()}`;
    sessionStorage.setItem(storage(owner, postId), key);
    return check({ post: postId, key }, true);
  }, [check, owner]);

  const dismiss = useCallback(() => {
    if (!pending || suggestion === null) return;
    sessionStorage.removeItem(storage(owner, pending.post));
    setSuggestion(null);
    setPending(null);
  }, [owner, pending, suggestion]);

  const changedScope = owner !== scope.current.owner
    || (scope.current.post !== undefined && scope.current.post !== post);
  return { waiting: !changedScope && waiting, suggestion: changedScope ? null : suggestion,
    error: changedScope ? null : error, pending: !changedScope && pending !== null, start, dismiss,
    retry: () => { if (pending) void check(pending); } };
}
