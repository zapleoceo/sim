import type { PreviewResponse } from '@/lib/models';
import { INITIAL_PREVIEW, historyOf, previewReducer } from './preview-chat';

const reply = (text: string): PreviewResponse => ({ reply: text, decision: null, guard: null, model: 'm', latency_ms: 12 });

describe('preview chat state', () => {
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

  it('a new message replaces the unanswered one; reset clears everything', () => {
    let s = previewReducer(INITIAL_PREVIEW, { type: 'send', text: 'a' });
    s = previewReducer(s, { type: 'fail', text: 'a', history: [], error: 'x' });
    s = previewReducer(s, { type: 'send', text: 'b' });
    expect(s.messages.map((m) => m.text)).toEqual(['b']);
    expect(s.failure).toBeNull();
    expect(previewReducer(s, { type: 'reset' })).toEqual(INITIAL_PREVIEW);
  });
});
