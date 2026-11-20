import { afterEach, describe, expect, it } from 'vitest';
import { fakeApi, reply } from '@/test/fake-api';
import { rememberPreview, pendingPreview, forgetPreview, pollPreview, TerminalPreviewError } from './preview-operation';
import type { PreviewInput } from './preview-operation';

const key = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
const route = `/api/personas/1/preview-operations/${key}`;
const input: PreviewInput = {
  message: 'Привет', stage: 'first_contact', status: 'active', needs: [], next_step: '',
  history: [], voice_input: false, stages: { schedule: false, debounce: false, decision_context: true },
};
const done = {
  status: 'complete', message: input.message, history: [],
  result: { reply: 'Здравствуйте', model: 'fake', latency_ms: 123, decision: null },
};

afterEach(() => sessionStorage.clear());

describe('async preview polling', () => {
  it('looks up the same operation when the POST response was lost', async () => {
    const log = fakeApi({ [`POST ${route}`]: reply(504), [`GET ${route}`]: done });
    await expect(pollPreview(1, key, new AbortController().signal, input)).resolves.toEqual(done);
    expect(log.map(({ method, path }) => [method, path])).toEqual([['POST', route], ['GET', route]]);
  });

  it('restores the same ID after refresh without posting the input', async () => {
    const log = fakeApi({ [`GET ${route}`]: done });
    rememberPreview(1, key);
    expect(Object.values(sessionStorage)).not.toContain(input.message);
    expect(pendingPreview(2)).toBeNull();
    await expect(pollPreview(1, pendingPreview(1)!, new AbortController().signal)).resolves.toEqual(done);
    forgetPreview(1, key);
    expect(pendingPreview(1)).toBeNull();
    expect(log.map(({ method }) => method)).toEqual(['GET']);
  });

  it('recovers accepted lost acknowledgement after disabling admission without posting again', async () => {
    const log = fakeApi({ [`POST ${route}`]: reply(404), [`GET ${route}`]: done });
    rememberPreview(1, key, 41);
    expect(pendingPreview(1, 42)).toBeNull();
    await expect(pollPreview(1, pendingPreview(1, 41)!, new AbortController().signal)).resolves.toEqual(done);
    expect(log.map(({ method }) => method)).toEqual(['GET']);
  });

  it('checks the status before interpreting a POST 404', async () => {
    const log = fakeApi({ [`POST ${route}`]: reply(404), [`GET ${route}`]: done });
    await expect(pollPreview(1, key, new AbortController().signal, input)).resolves.toEqual(done);
    expect(log.map(({ method }) => method)).toEqual(['POST', 'GET']);
  });

  it('does not retry an operation with unknown execution outcome', async () => {
    const log = fakeApi({ [`GET ${route}`]: { status: 'unknown' } });
    await expect(pollPreview(1, key, new AbortController().signal)).rejects.toBeInstanceOf(TerminalPreviewError);
    expect(log.map(({ method }) => method)).toEqual(['GET']);
  });
});
