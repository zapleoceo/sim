import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ClientDetail, Lead, Passport } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderAtRoute, renderWithApp } from '@/test/render';
import { parseLeadsSearch } from '@/router';
import { LeadsPage } from './leads-page';
import { MergeWithButton } from './merge-with';

const PASSPORT: Passport = {
  text: '', name: '', age: null, gender: '', city: '', country: '', timezone: '', occupation: '', stage: 'new',
  temperature: 0, next_step: '', updated_at: null, locked_fields: [],
};

const lead = (id: number, name: string, messengers: string[], patch: Partial<Lead> = {}): Lead => ({
  id, persona_id: 1, name, username: null, phone: null, timezone: null, notes: '', ai_enabled: true, passport: PASSPORT,
  created_at: `2026-11-${String(10 + id).padStart(2, '0')}T10:00:00Z`, last_seen_at: null,
  persona: { id: 1, name: 'Марина', avatar_media_id: null }, messengers, chats_count: 1, messages_count: 1, ...patch,
});

const DIMA = lead(1, 'Дима', ['telegram_user', 'whatsapp'], { username: 'zapleosoft', phone: '+380994811889' });
const INSTAGRAM = lead(8, 'Dmitriy Zaporozhets', ['instagram'], { username: 'zapleo_ceo' });
const OTHER_WA = lead(9, 'Оксана', ['whatsapp']);

describe('«Объединить с…» in the lead card', () => {
  it('finds a lead of the same persona and merges it into this card after the confirmation', async () => {
    const card: ClientDetail = { ...DIMA, chats: [] };
    const log = fakeApi({
      'GET /api/clients': { items: [DIMA, INSTAGRAM, OTHER_WA] },
      'POST /api/clients/1/merge': { ...card, messengers: ['instagram', 'telegram_user', 'whatsapp'] },
    });
    await renderWithApp(<MergeWithButton client={card} />);
    await userEvent.click(screen.getByRole('button', { name: 'Объединить с…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Объединить с другим лидом' });
    await userEvent.type(within(dialog).getByRole('searchbox', { name: 'Поиск лида' }), 'zapleo');
    await waitFor(() => expect(log.some((r) => r.path === '/api/clients' && r.query.get('q') === 'zapleo'
      && r.query.get('persona_id') === '1')).toBe(true));

    const list = within(dialog).getByRole('list', { name: 'Лиды' });
    expect(within(list).queryByText('Дима')).not.toBeInTheDocument();          // not itself
    const oksana = within(list).getByRole('button', { name: /Оксана/ });
    expect(oksana).toBeDisabled();
    expect(oksana).toHaveTextContent('WhatsApp');

    await userEvent.click(within(list).getByRole('button', { name: /Dmitriy Zaporozhets/ }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Объединить «Dmitriy Zaporozhets» с «Дима»?' });
    await userEvent.click(within(confirm).getByRole('button', { name: 'Объединить' }));
    await waitFor(() => expect(log.find((r) => r.path === '/api/clients/1/merge')?.body).toEqual({ other_id: 8 }));
  });
});

describe('two ticked leads on the leads page', () => {
  it('merges the newer into the older one and explains what can not be merged', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [] },
      'GET /api/clients': { items: [INSTAGRAM, DIMA, OTHER_WA] },
      'POST /api/clients/1/merge': { ...DIMA, chats: [] },
    });
    await renderAtRoute(<LeadsPage />, { path: '/leads', url: '/leads', validateSearch: parseLeadsSearch });
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Выбрать Dmitriy Zaporozhets' }));
    const bar = screen.getByRole('region', { name: 'Выбранные лиды' });
    expect(bar).toHaveTextContent('Отметьте второго лида');
    expect(within(bar).getByRole('button', { name: 'Объединить' })).toBeDisabled();

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать Оксана' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать Дима' }));
    expect(bar).toHaveTextContent('оставьте отмеченными двух');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать Оксана' }));
    expect(bar).toHaveTextContent('Останется «Дима» (старший), «Dmitriy Zaporozhets» перейдёт в него.');

    await userEvent.click(within(bar).getByRole('button', { name: 'Объединить' }));
    await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Объединить' }));
    await waitFor(() => expect(log.find((r) => r.path === '/api/clients/1/merge')?.body).toEqual({ other_id: 8 }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Выбранные лиды' })).not.toBeInTheDocument());
  });

  it('two leads in one messenger can not be merged', async () => {
    fakeApi({ 'GET /api/personas': { items: [] }, 'GET /api/clients': { items: [DIMA, OTHER_WA] } });
    await renderAtRoute(<LeadsPage />, { path: '/leads', url: '/leads', validateSearch: parseLeadsSearch });
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Выбрать Дима' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать Оксана' }));
    const bar = screen.getByRole('region', { name: 'Выбранные лиды' });
    expect(bar).toHaveTextContent('У обоих есть аккаунт в WhatsApp');
    expect(within(bar).getByRole('button', { name: 'Объединить' })).toBeDisabled();
  });
});
