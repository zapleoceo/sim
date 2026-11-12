import type { PreviewAttachment, PreviewResponse } from '@/lib/models';
import { INITIAL_PREVIEW, attachmentText, historyOf, isIdle, previewReducer } from './preview-chat';
import { recordingType } from './use-voice-recorder';

describe('voice recorder', () => {
  it('picks Opus in WebM, then OGG, then MP4 (Safari)', () => {
    expect(recordingType(() => true)).toEqual({ mime: 'audio/webm;codecs=opus', ext: 'webm' });
    expect(recordingType((t) => t === 'audio/mp4')).toEqual({ mime: 'audio/mp4', ext: 'm4a' });
    expect(recordingType(() => false)).toEqual({ mime: '', ext: 'webm' });
  });
});

const reply = (text: string): PreviewResponse => ({ reply: text, decision: null, guard: null, model: 'm', latency_ms: 12 });

describe('preview chat state', () => {
  it('carries approved context and preserves it on a rejected turn', () => {
    const next = { stage: 'engaged' as const, status: 'active' as const, needs: ['цель'], next_step: 'уточнить' };
    let state = previewReducer(INITIAL_PREVIEW, { type: 'send', text: 'привет' });
    state = previewReducer(state, { type: 'success', reply: { ...reply('ответ'), context: next } });
    expect(state.context).toEqual(next);
    state = previewReducer(state, { type: 'send', text: 'ещё' });
    state = previewReducer(state, { type: 'success', reply: { ...reply(''), context: next,
      guard: { verdict: 'blocked', violations: ['ошибка'], model: null, original_text: null } } });
    expect(state.context).toEqual(next);
  });
  it('appends the user turn, then the reply with model and latency', () => {
    let s = previewReducer(INITIAL_PREVIEW, { type: 'send', text: 'привет' });
    expect(s.busy).toBe(true);
    s = previewReducer(s, { type: 'success', reply: reply('здравствуйте') });
    expect(s.busy).toBe(false);
    expect(s.messages.map((m) => [m.role, m.text])).toEqual([['user', 'привет'], ['assistant', 'здравствуйте']]);
    expect(s.messages[1]?.meta).toBe('m · 12 мс');
    expect(historyOf(s.messages)).toEqual([
      { role: 'user', text: 'привет' },
      { role: 'assistant', text: 'здравствуйте' },
    ]);
  });

  it('keeps a failed message visible, leaves it out of history and retries it', () => {
    let s = previewReducer(INITIAL_PREVIEW, { type: 'send', text: 'вопрос' });
    s = previewReducer(s, { type: 'fail', text: 'вопрос', history: [], error: 'Сервис временно недоступен' });
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0]?.failed).toBe(true);
    expect(s.failure).toEqual({ text: 'вопрос', history: [], error: 'Сервис временно недоступен' });
    expect(historyOf(s.messages)).toEqual([]);

    s = previewReducer(s, { type: 'retry' });
    expect(s.busy).toBe(true);
    expect(s.failure).toBeNull();
    s = previewReducer(s, { type: 'success', reply: reply('ответ') });
    expect(historyOf(s.messages)).toEqual([
      { role: 'user', text: 'вопрос' },
      { role: 'assistant', text: 'ответ' },
    ]);
  });

  it('a voice note is sent as its transcript and the reply keeps its voice note', () => {
    const voice: PreviewAttachment = { kind: 'voice', name: 'voice.webm', text: 'привет', media_url: '/api/media/a.webm' };
    let s = previewReducer(INITIAL_PREVIEW, { type: 'upload' });
    expect(isIdle(s)).toBe(false);
    s = previewReducer(s, { type: 'send', text: attachmentText('', voice), attachment: voice });
    expect(s.uploading).toBe(false);
    expect(s.messages[0]).toEqual({ role: 'user', text: 'привет', attachment: voice });
    s = previewReducer(s, { type: 'success', reply: { ...reply('ага'), voice: { media_url: '/v.ogg', duration_s: 1, error: null } } });
    expect(s.messages[1]?.voice?.media_url).toBe('/v.ogg');
    expect(historyOf(s.messages)).toEqual([{ role: 'user', text: 'привет' }, { role: 'assistant', text: 'ага' }]);
    expect(attachmentText(' смотри ', { ...voice, kind: 'image', text: '[изображение: кот]' })).toBe('смотри\n[изображение: кот]');
  });

  it('an unreadable file shows an error and leaves the chat idle', () => {
    const s = previewReducer(previewReducer(INITIAL_PREVIEW, { type: 'upload' }), { type: 'upload-fail', error: 'нельзя' });
    expect(s.uploadError).toBe('нельзя');
    expect(isIdle(s)).toBe(true);
    expect(s.messages).toEqual([]);
  });

  it('remembers that a failed message was a voice note, for the retry', () => {
    const s = previewReducer(previewReducer(INITIAL_PREVIEW, { type: 'send', text: 'a' }),
      { type: 'fail', text: 'a', history: [], error: 'x', voiceInput: true });
    expect(s.failure?.voiceInput).toBe(true);
  });

  it('a new message replaces the unanswered one; reset clears everything', () => {
    let s = previewReducer(INITIAL_PREVIEW, { type: 'send', text: 'a' });
    s = previewReducer(s, { type: 'fail', text: 'a', history: [], error: 'x' });
    s = previewReducer(s, { type: 'send', text: 'b' });
    expect(s.messages.map((m) => m.text)).toEqual(['b']);
    expect(s.failure).toBeNull();
    expect(previewReducer(s, { type: 'reset' })).toEqual(INITIAL_PREVIEW);
  });
});
