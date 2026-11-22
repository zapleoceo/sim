import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ChatDay, ChatDaysResponse, ChatRow, ClientDetail, Passport } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { DaysTab } from './days-tab';

const PASSPORT: Passport = {
  text: '', name: '', age: null, gender: '', city: '', country: '', timezone: '', occupation: '', stage: 'new',
  temperature: 0, next_step: '', updated_at: null, locked_fields: [],
};

function chat(id: number, persona: string): ChatRow {
  return {
    id, client: { id: 1, name: 'Дима', username: 'dima', ai_enabled: true },
    persona: { id, name: persona, avatar_media_id: null }, ai_enabled: true, attention_instant: false, no_refusals: false,
    calls_out: false, needs_operator: false, unread_count: 0,
    persona_status: { code: 'answered', tone: 'ok', title: 'Всё отвечено', detail: '', until: null, local_until: null },
    last_message_at: null, last_message: null,
  };
}

const CLIENT: ClientDetail = {
  id: 1, persona_id: 1, name: 'Дима', username: 'dima', phone: null, timezone: null, notes: '', ai_enabled: true,
  passport: PASSPORT, created_at: '2026-11-01T10:00:00Z', last_seen_at: null,
  persona: { id: 1, name: 'Марина', avatar_media_id: null }, chats: [chat(7, 'Марина'), chat(8, 'Оля')],
};

function day(patch: Partial<ChatDay>): ChatDay {
  return {
    day: '2026-11-20', summary: 'Дима рассказал, что он врач.', facts: ['врач'], source: 'model', model: 'sol',
    error: null, first_event_id: 1, last_event_id: 5, updated_at: '2026-11-21T00:05:00Z', ...patch,
  };
}

const DAYS: ChatDaysResponse = {
  today: '2026-11-21',
  items: [day({}), day({ day: '2026-11-19', summary: 'Познакомились.', facts: [], source: 'edited' })],
  weeks: [{ week: '2026-11-09', summary: 'Первая неделя.', facts: [], source: 'model', model: 'sol', error: null,
    first_event_id: 1, last_event_id: 2, updated_at: '2026-11-16T00:10:00Z' }],
};

describe('DaysTab', () => {
  it('lists the days of the chosen chat, newest first, with the weeks', async () => {
    const log = fakeApi({
      'GET /api/chats/7/days': DAYS,
      'GET /api/chats/8/days': { today: '2026-11-21', items: [], weeks: [] },
    });
    await renderWithApp(<DaysTab client={CLIENT} />);

    const friday = await screen.findByRole('listitem', { name: 'пятница, 20 ноября' });
    expect(within(friday).getByText('Летописец')).toBeInTheDocument();
    expect(within(friday).getByLabelText('Итог дня')).toHaveValue('Дима рассказал, что он врач.');
    expect(within(friday).getByLabelText('Факты')).toHaveValue('врач');
    const thursday = screen.getByRole('listitem', { name: 'четверг, 19 ноября' });
    expect(within(thursday).getByText('изменён вручную')).toBeInTheDocument();
    expect(screen.getByText('Неделя с 9 ноября')).toBeInTheDocument();
    expect(screen.getByText('Первая неделя.')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Чат' }), '8');
    expect(await screen.findByText('Итогов дней пока нет')).toBeInTheDocument();
    expect(log.some((r) => r.path === '/api/chats/8/days')).toBe(true);
  });

  it('saves an edit as the owner and keeps the typed text local', async () => {
    const log = fakeApi({
      'GET /api/chats/7/days': DAYS,
      'PUT /api/chats/7/days/2026-11-20': (req) => day({ ...(req.body as object), source: 'edited' }),
    });
    await renderWithApp(<DaysTab client={{ ...CLIENT, chats: [chat(7, 'Марина')] }} />);
    const friday = await screen.findByRole('listitem', { name: 'пятница, 20 ноября' });
    expect(screen.queryByRole('combobox', { name: 'Чат' })).not.toBeInTheDocument();

    const summary = within(friday).getByLabelText('Итог дня');
    await userEvent.clear(summary);
    await userEvent.type(summary, 'Дима врач, у него дочь.');
    await userEvent.type(within(friday).getByLabelText('Факты'), '{enter}дочь Аня');
    await userEvent.click(within(friday).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    const put = log.find((r) => r.method === 'PUT');
    expect(put?.body).toEqual({ summary: 'Дима врач, у него дочь.', facts: ['врач', 'дочь Аня'] });
  });

  it('rebuilds a day (asking first for an edited one) and refreshes the passport', async () => {
    const log = fakeApi({
      'GET /api/chats/7/days': DAYS,
      'POST /api/chats/7/days/2026-11-19/rebuild': { queued: true, day: '2026-11-19' },
      'POST /api/chats/7/passport/refresh': { queued: true },
    });
    await renderWithApp(<DaysTab client={CLIENT} />);
    const thursday = await screen.findByRole('listitem', { name: 'четверг, 19 ноября' });

    await userEvent.click(within(thursday).getByRole('button', { name: 'Пересобрать день' }));
    const dialog = await screen.findByRole('alertdialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Пересобрать' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Обновить паспорт сейчас' }));

    await waitFor(() => expect(log.filter((r) => r.method === 'POST').map((r) => r.path)).toEqual([
      '/api/chats/7/days/2026-11-19/rebuild', '/api/chats/7/passport/refresh']));
  });
});
