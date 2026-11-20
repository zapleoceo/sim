import { useCallback, useEffect, useRef, useState } from 'react';
import { pollQueued, PreviewOperationError, TerminalPreviewError } from '@/lib/api/queued-operation';
import { errorMessage } from '@/lib/errors';
import type { PreviewAttachment } from '@/lib/models';
import { newPreviewKey } from './preview-operation';

type Result = { status: 'complete'; result: PreviewAttachment };

export function usePreviewAttachmentQueue(persona: number, owner: number) {
  const storage = `sim.preview.attachment.${owner}.${persona}`;
  const active = useRef<AbortController | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [ready, setReady] = useState<PreviewAttachment | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async (id: string, form?: FormData) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setKey(id);
    setWaiting(true);
    setError(null);
    try {
      const path = `/api/personas/${persona}/preview/attachment-operations/${encodeURIComponent(id)}`;
      const item = await pollQueued<Result>(path, controller.signal, form);
      if (!controller.signal.aborted) setReady(item.result);
    } catch (e) {
      if (controller.signal.aborted) return;
      if (e instanceof TerminalPreviewError) {
        sessionStorage.removeItem(storage);
        setKey(null);
      }
      setError(e instanceof PreviewOperationError ? e.message : errorMessage(e));
    } finally {
      if (active.current === controller) {
        active.current = null;
        setWaiting(false);
      }
    }
  }, [persona, storage]);

  useEffect(() => {
    const id = sessionStorage.getItem(storage);
    if (id) void check(id);
    return () => {
      active.current?.abort();
      active.current = null;
    };
  }, [check, storage]);

  const start = useCallback((file: Blob, name: string, voice: boolean) => {
    if (active.current || sessionStorage.getItem(storage)) return;
    const id = newPreviewKey();
    const form = new FormData();
    form.append('file', file, name);
    form.append('voice', String(voice));
    sessionStorage.setItem(storage, id);
    void check(id, form);
  }, [check, storage]);

  const clear = useCallback(() => {
    if (active.current) return;
    sessionStorage.removeItem(storage);
    setReady(null);
    setKey(null);
    setError(null);
  }, [storage]);

  return { waiting, ready, error, pending: key !== null, start, clear,
    retry: () => { if (key) void check(key); } };
}
