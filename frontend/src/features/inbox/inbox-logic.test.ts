import type { DialogDetail, DialogRow, Message } from '@/lib/models';
import {
  DEFAULT_DIALOG_FILTER, activeFilterCount, appendPage, deliveryMeta, detailChanged, groupByDay, isSendKey, mergeFirstPage,
  normalizeFilter, rowPreview, testDialogRow, visibleTestRows, combineDialogRows,
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
