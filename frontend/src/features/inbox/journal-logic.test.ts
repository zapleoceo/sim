import type { DialogEvent, Message } from '@/lib/models';
import { groupFeedByDay } from './inbox-logic';
import { NO_REASON, eventValue, mergeFeed, toEventVm } from './journal-logic';

function message(id: number, at: string): Message {
  return { id, direction: 'in', author: 'client', text: `m${id}`, created_at: at, delivery: 'sent' };
}

function event(id: number, at: string, patch: Partial<DialogEvent> = {}): DialogEvent {
  return { id, kind: 'stage', from: 'engaged', to: 'interested', actor: 'operator', actor_name: 'Оля', reason: '',
    created_at: at, ...patch };
}

describe('communication journal in the feed', () => {
  it('puts each service line between the messages by time and keeps the message order', () => {
    const feed = mergeFeed(
      [message(1, '2026-11-11T09:00:00Z'), message(2, '2026-11-11T09:10:00Z')],
      [event(8, '2026-11-11T09:20:00Z'), event(7, '2026-11-11T09:05:00Z'), event(6, '2026-11-11T08:00:00Z')],
    );
    expect(feed.map((e) => (e.type === 'message' ? `m${e.message.id}` : `e${e.event.id}`))).toEqual(
      ['e6', 'm1', 'e7', 'm2', 'e8']);
    expect(mergeFeed([message(1, '2026-11-11T09:00:00Z')])).toHaveLength(1);
    expect(mergeFeed([], [event(1, '2026-11-11T09:00:00Z')])).toHaveLength(1);   // a cleared chat keeps its journal
  });

  it('groups messages and service lines of one day together', () => {
    const groups = groupFeedByDay(
      [message(1, '2026-11-10T12:00:00Z'), message(2, '2026-11-11T12:00:00Z')],
      [event(5, '2026-11-11T11:00:00Z')],
    );
    expect(groups.map((g) => g.items.map((i) => i.type))).toEqual([['message'], ['event', 'message']]);
  });

  it('names what changed, who did it and why', () => {
    const vm = toEventVm(event(1, '2026-11-11T09:05:00Z', { reason: ' созвон ' }));
    expect(vm).toMatchObject({ tag: 'Этап', change: 'Вовлечён → Интерес', who: 'оператор Оля', reason: 'созвон', hasReason: true });
  });

  it('says that no reason was given instead of making one up', () => {
    const vm = toEventVm(event(1, '2026-11-11T09:05:00Z', { kind: 'ai_pause', from: 'on', to: 'paused', actor: 'system', actor_name: '' }));
    expect(vm).toMatchObject({ tag: 'ИИ', change: 'включён → на паузе', who: 'система', reason: NO_REASON, hasReason: false });
  });

  it('shows the course of a call as its steps, one per line', () => {
    const vm = toEventVm(event(1, '2026-11-11T09:05:00Z', { kind: 'call_steps', from: '', to: '', actor: 'system', actor_name: '',
      reason: '+0,0 с · Входящий звонок\n\n  +6,3 с · Пауза перед ответом 6,3 с  \n+7,1 с · Трубка снята' }));
    expect(vm).toMatchObject({ tag: 'Ход звонка', change: '', who: 'система', hasReason: true });
    expect(vm.steps).toEqual(['+0,0 с · Входящий звонок', '+6,3 с · Пауза перед ответом 6,3 с', '+7,1 с · Трубка снята']);
    expect(toEventVm(event(2, '2026-11-11T09:05:00Z', { reason: 'a\nb' })).steps).toEqual([]);
  });

  it('reads stored values as the operator knows them and passes the unknown through', () => {
    expect(eventValue('status', 'no_response')).toBe('Нет ответа');
    expect(eventValue('handoff', 'operator')).toBe('диалог ждёт оператора');
    expect(eventValue('model', 'gemini/flash')).toBe('gemini/flash');
    expect(toEventVm(event(1, '2026-11-11T09:05:00Z', { kind: 'resolved', from: '', to: '' })).change).toBe('');
    expect(toEventVm(event(1, '2026-11-11T09:05:00Z', { kind: 'future_kind', from: '', to: 'x', actor: 'robot' })))
      .toMatchObject({ tag: 'future_kind', change: 'x', who: 'robot Оля' });
  });
});
