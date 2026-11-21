import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Arc, CanonFact, DayEvent, DaysResponse, PersonaLife } from '@/lib/types';
import { fakeApi, type FakeRequest } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { contextBlocks } from '@/features/chats/reply-run-sheet';
import { LifeSection } from './life-section';
import { newDayEvent } from './life-logic';

const ev = (start: string, end: string, title: string, extra: Partial<DayEvent> = {}): DayEvent => ({
  ...newDayEvent(), start, end, title, ...extra,
});

const LIFE: PersonaLife = {
  routine: [{
    days: [0, 1, 2, 3, 4, 5, 6], start: '23:00', end: '06:30', activity: 'сон', place: 'дома', with_whom: [],
    busy: 'away', outfit: '', ambience: null,
  }],
  people: [{ name: 'Андрей', relation: 'муж', traits: '', contact: '', notes: '' }],
  hobbies: [],
  places: [],
};

const DAYS: DaysResponse = {
  today: '2026-11-21',
  now_minute: 19 * 60 + 30,
  items: [{
    day: '2026-11-21', source: 'model', model: 'life-model', error: null, summary: '', updated_at: '2026-11-20T02:00:00Z',
    events: [
      ev('08:00', '09:00', 'завтрак с Андреем', { with_whom: ['Андрей'], outfit: 'серый свитер' }),
      ev('19:00', '21:00', 'ужин и сериал', { place: 'дома', details: 'третья серия' }),
      ev('21:30', '23:00', 'чтение Канемана'),
    ],
  }],
};

const ARC: Arc = {
  id: 3, title: 'Ремонт в ванной', summary: 'Меняют ванну', stage: 'плитка в коридоре', status: 'active',
  started_on: '2026-11-01', ends_on: null, updated_at: '2026-11-20T10:00:00Z',
};

const CANON: CanonFact = {
  id: 7, fact: 'Соседскую собаку зовут Рекс', status: 'active', chat_id: 1, event_id: 5, created_at: '2026-11-20T10:00:00Z',
};

function backend(extra: Record<string, (req: FakeRequest) => unknown> = {}): FakeRequest[] {
  return fakeApi({
    'GET /api/persona/life': LIFE,
    'GET /api/persona/days': DAYS,
    'GET /api/persona/arcs': { items: [ARC] },
    'GET /api/persona/canon': { items: [CANON] },
    ...extra,
  });
}

describe('LifeSection', () => {
  it('shows today with the current event and re-plans tomorrow', async () => {
    const log = backend({ 'POST /api/persona/days/2026-11-22/plan': () => ({ queued: true, day: '2026-11-22' }) });
    await renderWithApp(<LifeSection />);

    const today = await screen.findByRole('region', { name: /Сегодня: суббота, 21 ноября/ });
    const current = within(today).getByText('ужин и сериал').closest('li');
    expect(current).toHaveAttribute('aria-current', 'true');
    expect(within(today).getByText('сейчас')).toBeInTheDocument();
    expect(within(today).getByText('план модели')).toBeInTheDocument();

    const tomorrow = screen.getByRole('region', { name: /Завтра: воскресенье, 22 ноября/ });
    expect(within(tomorrow).getByText('не спланирован')).toBeInTheDocument();
    await userEvent.click(within(tomorrow).getByRole('button', { name: 'Спланировать заново' }));
    await waitFor(() => expect(log.some((r) => r.method === 'POST' && r.path.endsWith('/2026-11-22/plan'))).toBe(true));
  });

  it('edits a day by hand and checks overlaps first', async () => {
    const log = backend({ 'PUT /api/persona/days/2026-11-21': (req) => ({ ...DAYS.items[0], ...(req.body as object), source: 'edited' }) });
    await renderWithApp(<LifeSection />);
    const today = await screen.findByRole('region', { name: /Сегодня/ });
    await userEvent.click(within(today).getByRole('button', { name: 'Изменить' }));

    // A time input takes a whole HH:MM value at once.
    const start = within(today).getByLabelText('С, строка 2');
    fireEvent.change(start, { target: { value: '08:30' } });
    await userEvent.click(within(today).getByRole('button', { name: 'Сохранить день' }));
    expect(await within(today).findByText(/накладывается на предыдущее/)).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'PUT')).toHaveLength(0);

    fireEvent.change(start, { target: { value: '18:00' } });
    await userEvent.click(within(today).getByRole('button', { name: 'Сохранить день' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    const put = log.find((r) => r.method === 'PUT');
    expect((put?.body as { events: DayEvent[] }).events[1]?.start).toBe('18:00');
  });

  it('saves the world with one button', async () => {
    const log = backend({ 'PUT /api/persona/life': (req) => req.body });
    await renderWithApp(<LifeSection />);
    await userEvent.click(await screen.findByRole('tab', { name: 'Мир' }));

    const save = await screen.findByRole('button', { name: 'Сохранить мир' });
    expect(save).toBeDisabled();
    const activity = screen.getByLabelText('Что делает, строка 1');
    await userEvent.clear(activity);
    await userEvent.type(activity, 'сон до будильника');
    await userEvent.click(screen.getByRole('button', { name: 'вс' }));          // Sunday off
    expect(screen.getByText('Мир изменён, не сохранён')).toBeInTheDocument();
    await userEvent.click(save);

    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    const body = log.find((r) => r.method === 'PUT')?.body as PersonaLife;
    expect(body.routine[0]?.activity).toBe('сон до будильника');
    expect(body.routine[0]?.days).toEqual([0, 1, 2, 3, 4, 5]);
    expect(body.people).toEqual(LIFE.people);
    expect(await screen.findByText('Мир сохранён')).toBeInTheDocument();
  });

  it('adds a story arc and rejects a canon fact', async () => {
    const log = backend({
      'POST /api/persona/arcs': (req) => ({ ...ARC, id: 4, ...(req.body as object) }),
      'PATCH /api/persona/canon/7': (req) => ({ ...CANON, ...(req.body as object) }),
    });
    await renderWithApp(<LifeSection />);
    expect(await screen.findByDisplayValue('Ремонт в ванной')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Новая история'), 'Поездка в Грузию');
    await userEvent.click(screen.getByRole('button', { name: 'Добавить историю' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(1));
    expect(log.find((r) => r.method === 'POST')?.body).toEqual({ title: 'Поездка в Грузию', summary: '', stage: '' });

    await userEvent.click(screen.getByRole('button', { name: 'Отклонить: Соседскую собаку зовут Рекс' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PATCH')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PATCH')?.body).toEqual({ status: 'rejected' });
  });
});

describe('reply run context', () => {
  it('shows the life blocks right after the facts', () => {
    const blocks = contextBlocks({ client: 'c', canon: 'k', facts: 'f', now: 'n', routine: 'r', arcs: 'a' });
    expect(blocks.map((b) => b.title)).toEqual([
      'Факты от кода', 'Распорядок', 'Точные данные на сейчас', 'Истории', 'Канон', 'Карточка клиента',
    ]);
  });
});
