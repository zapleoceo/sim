import type { DialogDetail, DialogRow, Message } from '@/lib/models';
import {
  CLEAR_CHAT_CONFIRM, DEFAULT_DIALOG_FILTER, activeFilterCount, appendPage, clearedRow, deliveryMeta, detailChanged, groupByDay, isSendKey, mergeFirstPage,
  messagesWord, normalizeFilter, pauseNotice, recoveryToast, rowPreview, testDialogRow, visibleTestRows, combineDialogRows,
} from './inbox-logic';

function row(id: number, patch: Partial<DialogRow> = {}): DialogRow {
  return {
    id,
    channel_kind: 'telegram',
    persona: { id: 1, name: 'Марина', avatar_url: '' },
    client: { id, display_name: `C${id}`, avatar_url: '', stage: 'engaged', status: 'active' },
    last_message: null,
    unread: 0,
    ai_paused: false,
    needs_operator: false,
    ...patch,
  };
}

function msg(id: number, at: string, patch: Partial<Message> = {}): Message {
  return { id, direction: 'in', author: 'client', text: `m${id}`, created_at: at, delivery: 'sent', ...patch };
}

describe('inbox filters', () => {
  it('counts only filters that differ from defaults', () => {
    expect(activeFilterCount(DEFAULT_DIALOG_FILTER)).toBe(0);
    expect(activeFilterCount({ ...DEFAULT_DIALOG_FILTER, q: '   ' })).toBe(0);
    expect(activeFilterCount({ ...DEFAULT_DIALOG_FILTER, q: 'ann', persona_id: 3, unread_only: true })).toBe(3);
  });

  it('trims the search query for the API', () => {
    expect(normalizeFilter({ ...DEFAULT_DIALOG_FILTER, q: '  anna ' }).q).toBe('anna');
  });
});

describe('list paging', () => {
  it('poll replaces the first page and keeps the loaded tail without duplicates', () => {
    const current = [row(1), row(2), row(3), row(4)];
    const merged = mergeFirstPage(current, [row(4), row(1)], false, 2);
    expect(merged.map((r) => r.id)).toEqual([4, 1, 3]);
  });

  it('filter change drops the tail', () => {
    expect(mergeFirstPage([row(1), row(2), row(3)], [row(9)], true, 2).map((r) => r.id)).toEqual([9]);
  });

  it('appends the next page skipping rows already shown', () => {
    expect(appendPage([row(1), row(2)], [row(2), row(3)]).map((r) => r.id)).toEqual([1, 2, 3]);
  });

  it('previews the last message with «Вы:» for outgoing', () => {
    expect(rowPreview(row(1))).toBe('Нет сообщений');
    expect(rowPreview(row(1, { last_message: { text: 'hi', direction: 'out', at: '2026-01-01T10:00:00Z' } }))).toBe('Вы: hi');
  });
});

describe('conversation', () => {
  it('groups messages by day and puts client on the left', () => {
    const now = new Date(2026, 10, 11, 12, 0);
    const today = new Date(2026, 10, 11, 9, 0).toISOString();
    const earlier = new Date(2026, 10, 1, 9, 0).toISOString();
    const groups = groupByDay([msg(1, earlier), msg(2, today), msg(3, today, { author: 'ai', direction: 'out' })], now);
    expect(groups).toHaveLength(2);
    expect(groups[1].label).toBe('Сегодня');
    expect(groups[1].items.map((i) => [i.side, i.authorLabel])).toEqual([
      ['left', ''],
      ['right', 'ИИ'],
    ]);
  });

  it('explains a cancelled message blocked by the guard', () => {
    expect(deliveryMeta('cancelled', { verdict: 'blocked', violations: [], model: null, original_text: null }).label).toBe(
      'Не отправлено: заблокировано контролером',
    );
    expect(deliveryMeta('cancelled').label).toBe('Отменено: диалог перешёл к человеку');
    expect(deliveryMeta('failed').icon).toBe('alert');
  });

  it('detects real changes of a polled dialog only', () => {
    const base: DialogDetail = {
      dialog: { id: 1, ai_paused: false, needs_operator: false },
      client: {} as DialogDetail['client'],
      messages: [msg(1, '2026-01-01T10:00:00Z')],
    };
    expect(detailChanged(null, base)).toBe(true);
    expect(detailChanged(base, { ...base })).toBe(false);
    expect(detailChanged(base, { ...base, dialog: { ...base.dialog, ai_paused: true } })).toBe(true);
    const waiting = { count: 1, since: '2026-01-01T10:00:00Z', message_ids: [1] };
    expect(detailChanged(base, { ...base, dialog: { ...base.dialog, unanswered: waiting } })).toBe(true);
  });

  it('sends on Enter but not on Shift+Enter or while composing', () => {
    expect(isSendKey({ key: 'Enter', shiftKey: false })).toBe(true);
    expect(isSendKey({ key: 'Enter', shiftKey: true })).toBe(false);
    expect(isSendKey({ key: 'Enter', shiftKey: false, isComposing: true })).toBe(false);
  });
});


describe('test conversations in the inbox', () => {
  const branch = {
    id: 7, admin: { id: 1, display_name: 'Админ' }, persona: { id: 3, name: 'Марина' },
    title: 'Пробный разговор', message_count: 2, stage: 'engaged' as const,
    status: 'active' as const, created_at: '2026-01-01T09:00:00Z',
    last_active_at: '2026-01-02T09:00:00Z', closed_at: null,
  };
  it('marks a test branch and never routes it to a real dialog id', () => {
    const test = testDialogRow(branch);
    expect(test.id).toBe(-7);
    expect(test.is_test).toBe(true);
    expect(test.client.stage).toBe('engaged');
  });
  it('filters test branches and merges them by activity time', () => {
    const filter = { ...DEFAULT_DIALOG_FILTER, source: 'test' as const };
    const test = visibleTestRows([branch], [], filter);
    expect(test).toHaveLength(1);
    expect(visibleTestRows([branch], [], { ...filter, avatar: 'with' })).toHaveLength(0);
    expect(visibleTestRows([branch], [], { ...filter, channel_id: 4 })).toHaveLength(0);
    expect(combineDialogRows([row(1, { last_message: { text: 'real', direction: 'in', at: '2026-01-01T09:00:00Z' } })], test)[0].id).toBe(-7);
  });
});

describe('AI pause', () => {
  it('says nothing while the AI is on', () => {
    expect(pauseNotice({ ai_paused: false })).toBeNull();
    expect(pauseNotice({ ai_paused: false, unanswered: { count: 2, since: null, message_ids: [1, 2] } })).toBeNull();
  });

  it('shows that the AI is off and how many client messages wait', () => {
    expect(pauseNotice({ ai_paused: true })).toEqual({
      waiting: 0, text: 'Персонаж не отвечает в этом диалоге, пока ИИ не включат.' });
    const waiting = pauseNotice({ ai_paused: true, unanswered: { count: 2, since: null, message_ids: [1, 2] } });
    expect(waiting).toEqual({ waiting: 2, text: 'Персонаж не отвечает в этом диалоге. Без ответа: 2 сообщения клиента.' });
  });

  it('declines the word for the count', () => {
    expect([1, 2, 5, 11, 21, 22, 112].map(messagesWord)).toEqual(
      ['сообщение', 'сообщения', 'сообщений', 'сообщений', 'сообщение', 'сообщения', 'сообщений']);
  });

  it('reports what resuming the AI did with the unanswered messages', () => {
    expect(recoveryToast({ status: 'queued', messages: 2 })).toEqual({
      tone: 'success', text: 'ИИ включён. Персонаж ответит одним сообщением, без ответа было: 2 сообщения.' });
    const stale = recoveryToast({ status: 'stale', messages: 1 });
    expect(stale.tone).toBe('warning');
    expect(stale.text).toMatch(/1 сообщение, они устарели: персонаж сам не ответит/);
    expect(recoveryToast({ status: 'refused', messages: 1 }).tone).toBe('warning');
    expect(recoveryToast({ status: 'none', messages: 0 })).toEqual({ tone: 'success', text: 'ИИ включён.' });
    expect(recoveryToast(undefined).text).toBe('ИИ включён.');
  });
});

describe('clear chat', () => {
  it('empties the list row of a cleared dialog and keeps who it is', () => {
    const before = row(3, { unread: 4, needs_operator: true, ai_paused: true,
      last_message: { text: 'старое', direction: 'in', at: '2026-01-01T09:00:00Z' } });
    expect(clearedRow(before)).toEqual({ ...before, last_message: null, unread: 0, needs_operator: false });
  });

  it('warns that the wipe is final and that the messenger keeps its messages', () => {
    expect(CLEAR_CHAT_CONFIRM.danger).toBe(true);
    expect(CLEAR_CHAT_CONFIRM.message).toMatch(/без возможности восстановления/);
    expect(CLEAR_CHAT_CONFIRM.message).toMatch(/В Telegram .*сообщения останутся/);
  });
});
