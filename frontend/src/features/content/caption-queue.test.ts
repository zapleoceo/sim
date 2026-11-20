import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeApi, reply } from '@/test/fake-api';
import { useCaptionQueue } from './caption-queue';

const key = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
const canonical = '1795171200000-d7dd86f1-1943-4fcb-bc89-8c839db95995';
const path = `/api/content/1/caption-operations/${key}`;

afterEach(() => sessionStorage.clear());

describe('caption queue', () => {
  it('restores through the submitted ID, adopts the canonical ID, and clears on scope changes', async () => {
    sessionStorage.setItem('sim.caption.pending.41.1', key);
    const log = fakeApi({
      [`GET ${path}`]: { id: canonical, status: 'complete', result: { caption: 'Приватная подпись' } },
    });
    const { result, rerender } = renderHook(
      ({ owner, post }) => useCaptionQueue(owner, post),
      { initialProps: { owner: 41, post: 1 } },
    );
    await waitFor(() => expect(result.current.suggestion).toBe('Приватная подпись'));
    expect(sessionStorage.getItem('sim.caption.pending.41.1')).toBe(canonical);
    expect(log.map(({ method }) => method)).toEqual(['GET']);
    rerender({ owner: 42, post: 1 });
    expect(result.current.suggestion).toBeNull();
    expect(result.current.error).toBeNull();
    rerender({ owner: 42, post: 2 });
    expect(result.current.suggestion).toBeNull();
    expect(log).toHaveLength(1);
  });

  it('clears a terminal error on switching posts', async () => {
    sessionStorage.setItem('sim.caption.pending.41.1', key);
    fakeApi({ [`GET ${path}`]: { status: 'unknown' } });
    const { result, rerender } = renderHook(({ post }) => useCaptionQueue(41, post), {
      initialProps: { post: 1 },
    });
    await waitFor(() => expect(result.current.error).toMatch(/Исход запроса неизвестен/));
    rerender({ post: 2 });
    expect(result.current.error).toBeNull();
  });

  it.each([true, false])('uses the canonical receipt or stops without reposting when it is lost (%s)', async (accepted) => {
    const canonicalPath = `/api/content/1/caption-operations/${canonical}`;
    const log = fakeApi({
      [`POST ${path}`]: accepted ? reply(202, { id: canonical }) : reply(504),
      [`GET ${canonicalPath}`]: { id: canonical, status: 'complete', result: { caption: 'Подпись' } },
      [`GET ${path}`]: { status: 'expired' },
    });
    const { result } = renderHook(() => useCaptionQueue(41, 1));
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1795171200000);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue('9d80c89d-438e-4eef-8848-7bfb39a89af3');
    try {
      await act(async () => result.current.start(1));
    } finally {
      clock.mockRestore();
      uuid.mockRestore();
    }
    expect(log.map(({ method, path: url }) => [method, url])).toEqual([
      ['POST', path], ['GET', accepted ? canonicalPath : path],
    ]);
    expect(result.current.suggestion).toBe(accepted ? 'Подпись' : null);
  });

  it('shows an unavailable queue without posting to the old endpoint', async () => {
    const log = fakeApi({ [`POST ${path}`]: reply(404, { detail: { status: 'queue_disabled' } }) });
    const { result } = renderHook(() => useCaptionQueue(41, 1));
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1795171200000);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue('9d80c89d-438e-4eef-8848-7bfb39a89af3');
    try {
      await act(async () => result.current.start(1));
    } finally {
      clock.mockRestore();
      uuid.mockRestore();
    }
    expect(result.current.error).toBe('Очередь генерации временно недоступна.');
    expect(log.map(({ method, path: url }) => [method, url])).toEqual([['POST', path], ['GET', path]]);
  });
});
