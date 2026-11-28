import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { Lead, Passport, PersonaRow } from '@/lib/types';
import { fakeApi, reply } from '@/test/fake-api';
import { renderAtRoute, renderWithApp } from '@/test/render';
import { parseLeadsSearch } from '@/router';
import { profilePatch } from './lead-page';
import { LeadsPage } from './leads-page';
import { PassportEditor } from './passport-editor';
import { passportFormErrors, passportPatch, passportPreview, toPassportForm, unlockPatch } from './passport-logic';

function passport(patch: Partial<Passport> = {}): Passport {
  return {
    text: '', name: '', age: null, gender: '', city: '', country: '', timezone: '', occupation: '', stage: 'new',
    temperature: 0, next_step: '', updated_at: null, locked_fields: [], ...patch,
  };
}

function lead(patch: Partial<Lead>): Lead {
  return {
    id: 1, persona_id: 1, name: 'Дима', username: 'dima', phone: '+380501112233', timezone: null, notes: '', ai_enabled: true,
    passport: passport({ stage: 'warm', temperature: 60, city: 'Киев', age: 34 }), created_at: '2026-11-01T10:00:00Z',
    last_seen_at: '2026-11-20T18:30:00Z', persona: { id: 1, name: 'Марина', avatar_media_id: null }, messengers: ['telegram_user'], chats_count: 1,
    messages_count: 12, ...patch,
  };
}

const PERSONAS: PersonaRow[] = [
  { id: 1, name: 'Марина', avatar_media_id: null, is_active: true, ai_enabled: true, leads_count: 1, channels: [] },
  { id: 2, name: 'Оля', avatar_media_id: null, is_active: true, ai_enabled: true, leads_count: 1, channels: [] },
];

/** The passport card with a button that plays the passport writer's live update. */
function LivePassport() {
  const [current, setCurrent] = useState(passport({ city: 'Киев', text: 'Кто он: Дима' }));
  return (
    <>
      <button type="button" onClick={() => setCurrent(passport({ city: 'Львов', text: 'Кто он: Дима, переехал', updated_at: '2026-11-22T03:00:00Z' }))}>
        паспортистка обновила
      </button>
      <PassportEditor clientId={7} passport={current} />
    </>
  );
}

const renderLeads = (url = '/leads') => renderAtRoute(<LeadsPage />, { path: '/leads', url, validateSearch: parseLeadsSearch });

describe('LeadsPage', () => {
  it('renders the full-width table with persona, stage, city, age and messages', async () => {
    fakeApi({
      'GET /api/personas': { items: PERSONAS },
      'GET /api/clients': {
        items: [lead({}), lead({ id: 2, name: '', username: 'anna', ai_enabled: false, persona: { id: 2, name: 'Оля', avatar_media_id: null } })],
      },
    });
    await renderLeads();

    const table = await screen.findByRole('table');
    expect(await within(table).findAllByText('Киев')).toHaveLength(2); // both leads live there
    expect(within(table).getAllByRole('row')).toHaveLength(3); // header + 2
    for (const header of ['Персонаж', 'Стадия', 'Город', 'Возраст', 'Последний контакт', 'Сообщений']) {
      expect(within(table).getByRole('columnheader', { name: header })).toBeInTheDocument();
    }
    expect(within(table).getByRole('link', { name: /Дима/ })).toHaveAttribute('href', '/leads/1');
    expect(within(table).getByRole('link', { name: /@anna/ })).toHaveAttribute('href', '/leads/2');
    expect(within(table).getAllByText('Тёплый')).toHaveLength(2);
    expect(within(table).getByText('Оля')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'ИИ отвечает клиенту @anna' })).not.toBeChecked();
  });

  it('filters by persona and stage and searches, keeping the filters in the address', async () => {
    const log = fakeApi({ 'GET /api/personas': { items: PERSONAS }, 'GET /api/clients': { items: [] } });
    const { router } = await renderLeads();
    expect(await screen.findByText('Лидов пока нет')).toBeInTheDocument();
    await screen.findByRole('option', { name: 'Оля' });

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Персонаж' }), '2');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Стадия' }), 'hot');
    await userEvent.type(screen.getByRole('searchbox', { name: 'Поиск лидов' }), 'ann');
    await waitFor(() =>
      expect(
        log.some((r) => r.path === '/api/clients' && r.query.get('persona_id') === '2' && r.query.get('stage') === 'hot'
          && r.query.get('q') === 'ann'),
      ).toBe(true),
    );
    expect(await screen.findByText('Никого не нашли')).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ persona: 2, stage: 'hot', q: 'ann' });

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Стадия' }), '');
    await waitFor(() => expect(router.state.location.search).toEqual({ persona: 2, q: 'ann' }));
  });

  it('opens with the filters of the address (a link, «назад» from a card)', async () => {
    const log = fakeApi({ 'GET /api/personas': { items: PERSONAS }, 'GET /api/clients': { items: [] } });
    await renderLeads('/leads?persona=2&stage=warm&q=anna');
    await screen.findByRole('option', { name: 'Оля' });
    expect(screen.getByRole('combobox', { name: 'Персонаж' })).toHaveValue('2');
    expect(screen.getByRole('combobox', { name: 'Стадия' })).toHaveValue('warm');
    expect(screen.getByRole('searchbox', { name: 'Поиск лидов' })).toHaveValue('anna');
    await waitFor(() =>
      expect(log.some((r) => r.path === '/api/clients' && r.query.get('persona_id') === '2' && r.query.get('stage') === 'warm'
        && r.query.get('q') === 'anna')).toBe(true),
    );
  });

  it('toggles AI inline with PATCH {ai_enabled}', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: PERSONAS },
      'GET /api/clients': { items: [lead({ ai_enabled: false })] },
      'PATCH /api/clients/1': lead({ ai_enabled: true }),
    });
    await renderLeads();
    await userEvent.click(await screen.findByRole('switch', { name: 'ИИ отвечает клиенту Дима' }));
    await waitFor(() => expect(log.find((r) => r.method === 'PATCH')?.body).toEqual({ ai_enabled: true }));
  });

  it('shows an error state with retry', async () => {
    fakeApi({ 'GET /api/personas': { items: [] }, 'GET /api/clients': reply(500, { error: 'база недоступна' }) });
    await renderLeads();
    expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });
});

describe('PassportEditor', () => {
  it('saves only the changed fields; the server locks them', async () => {
    const log = fakeApi({ 'PATCH /api/clients/7': lead({ id: 7 }) });
    await renderWithApp(<PassportEditor clientId={7} passport={passport({ city: 'Киев', text: 'Кто он: Дима' })} />);

    const save = screen.getByRole('button', { name: 'Сохранить паспорт' });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText('Стадия'), 'hot');
    const city = screen.getByLabelText('Город');
    await userEvent.clear(city);
    await userEvent.type(city, 'Львов');
    await userEvent.click(save);

    await waitFor(() => expect(log.filter((r) => r.method === 'PATCH')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PATCH')?.body).toEqual({ passport: { stage: 'hot', city: 'Львов' } });
  });

  it('follows a live passport update while untouched: nothing to save, nothing locked', async () => {
    const log = fakeApi({ 'PATCH /api/clients/7': lead({ id: 7 }) });
    await renderWithApp(<LivePassport />);
    await userEvent.click(screen.getByRole('button', { name: 'паспортистка обновила' }));
    expect(screen.getByLabelText('Город')).toHaveValue('Львов');
    expect(screen.getByLabelText('Паспорт')).toHaveValue('Кто он: Дима, переехал');
    expect(screen.getByRole('button', { name: 'Сохранить паспорт' })).toBeDisabled();
    expect(log.filter((r) => r.method === 'PATCH')).toHaveLength(0);
  });

  it('keeps an edit through a live update and sends (locks) only the edited field', async () => {
    const log = fakeApi({ 'PATCH /api/clients/7': lead({ id: 7 }) });
    await renderWithApp(<LivePassport />);
    await userEvent.selectOptions(screen.getByLabelText('Стадия'), 'hot');
    await userEvent.click(screen.getByRole('button', { name: 'паспортистка обновила' }));
    expect(screen.getByLabelText('Стадия')).toHaveValue('hot');

    await userEvent.click(screen.getByRole('button', { name: 'Сохранить паспорт' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PATCH')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PATCH')?.body).toEqual({ passport: { stage: 'hot' } });
  });

  it('shows locks and unlocks a field', async () => {
    const log = fakeApi({ 'PATCH /api/clients/7': lead({ id: 7 }) });
    await renderWithApp(<PassportEditor clientId={7} passport={passport({ stage: 'hot', city: 'Киев', locked_fields: ['stage', 'city'] })} />);
    expect(screen.getByRole('button', { name: 'Снять замок: Город' })).toBeInTheDocument();
    // The lock button sits next to the label, not inside it: the field's name stays «Город».
    expect(screen.getByRole('textbox', { name: 'Город' })).toHaveValue('Киев');
    await userEvent.click(screen.getByRole('button', { name: 'Снять замок: Стадия' }));
    await waitFor(() => expect(log.find((r) => r.method === 'PATCH')?.body).toEqual({ passport: { locked_fields: ['city'] } }));
  });

  it('blocks invalid numbers before sending', async () => {
    const log = fakeApi({});
    await renderWithApp(<PassportEditor clientId={7} passport={passport()} />);
    const temperature = screen.getByLabelText('Теплота');
    await userEvent.clear(temperature);
    await userEvent.type(temperature, '150');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить паспорт' }));
    expect(await screen.findByText(/Теплота — целое число от 0 до 100/)).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'PATCH')).toHaveLength(0);
  });
});

describe('passport logic', () => {
  it('patch converts types and skips unchanged fields', () => {
    const initial = toPassportForm(passport({ age: 30, temperature: 10 }));
    expect(passportPatch(initial, initial)).toEqual({});
    expect(passportPatch({ ...initial, age: '', temperature: '55', next_step: ' позвать ' }, initial)).toEqual({
      age: null, temperature: 55, next_step: 'позвать',
    });
    expect(unlockPatch(['stage', 'age'], 'age')).toEqual({ locked_fields: ['stage'] });
  });

  it('validates age, temperature and the time zone', () => {
    const form = toPassportForm(passport());
    expect(passportFormErrors(form)).toEqual([]);
    expect(passportFormErrors({ ...form, age: '0', temperature: 'x', timezone: 'Kyiv' })).toHaveLength(3);
    expect(passportFormErrors({ ...form, timezone: 'America/Argentina/Buenos_Aires' })).toEqual([]);
  });

  it('preview keeps the first lines', () => {
    expect(passportPreview('a\n\nb\nc', 2)).toBe('a\nb\n…');
  });

  it('profile patch sends only changed fields, empty phone as null', () => {
    const initial = { name: 'Дима', phone: '+380', notes: '' };
    expect(profilePatch({ ...initial, phone: ' ', notes: 'любит кофе' }, initial)).toEqual({ phone: null, notes: 'любит кофе' });
  });
});
