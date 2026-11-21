import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Client } from '@/lib/types';
import { fakeApi, reply } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { ClientsPage } from './clients-page';
import { memoryOps } from './memory-editor';
import { profilePatch } from './client-page';

function client(patch: Partial<Client>): Client {
  return {
    id: 1, name: 'Дима', username: 'dima', phone: '+380501112233', timezone: 'Europe/Kyiv', notes: '', ai_enabled: true,
    memory: [], created_at: '2026-11-01T10:00:00Z', last_seen_at: '2026-11-20T18:30:00Z', chats_count: 1, ...patch,
  };
}

describe('ClientsPage', () => {
  it('renders the full-width table with links to the client cards', async () => {
    fakeApi({
      'GET /api/clients': { items: [client({}), client({ id: 2, name: '', username: 'anna', phone: null, ai_enabled: false, chats_count: 3 })] },
    });
    await renderWithApp(<ClientsPage />);

    await screen.findByText('+380501112233');
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(3); // header + 2
    expect(within(table).getByRole('columnheader', { name: 'Последний контакт' })).toBeInTheDocument();
    expect(within(table).getByRole('link', { name: /Дима/ })).toHaveAttribute('href', '/clients/1');
    expect(within(table).getByRole('link', { name: /@anna/ })).toHaveAttribute('href', '/clients/2');
    expect(screen.getByRole('switch', { name: 'ИИ отвечает клиенту Дима' })).toBeChecked();
    expect(screen.getByRole('switch', { name: 'ИИ отвечает клиенту @anna' })).not.toBeChecked();
  });

  it('toggles AI inline with PATCH {ai_enabled}', async () => {
    const log = fakeApi({
      'GET /api/clients': { items: [client({ ai_enabled: false })] },
      'PATCH /api/clients/1': client({ ai_enabled: true }),
    });
    await renderWithApp(<ClientsPage />);
    await userEvent.click(await screen.findByRole('switch', { name: 'ИИ отвечает клиенту Дима' }));
    await waitFor(() => expect(log.find((r) => r.method === 'PATCH')?.body).toEqual({ ai_enabled: true }));
  });

  it('shows the empty state and the search query', async () => {
    const log = fakeApi({ 'GET /api/clients': { items: [] } });
    await renderWithApp(<ClientsPage />);
    expect(await screen.findByText('Клиентов пока нет')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('searchbox', { name: 'Поиск клиентов' }), 'ann');
    await waitFor(() => expect(log.some((r) => r.query.get('q') === 'ann')).toBe(true));
    expect(await screen.findByText('Никого не нашли')).toBeInTheDocument();
  });

  it('shows an error state with retry', async () => {
    fakeApi({ 'GET /api/clients': reply(500, { error: 'база недоступна' }) });
    await renderWithApp(<ClientsPage />);
    expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });
});

describe('client card logic', () => {
  it('memory add / edit / remove', () => {
    const now = new Date('2026-11-21T00:00:00Z');
    const one = memoryOps.add([], ' живёт в Варшаве ', now);
    expect(one).toEqual([{ fact: 'живёт в Варшаве', event_id: null, at: '2026-11-21T00:00:00.000Z' }]);
    expect(memoryOps.edit(one, 0, 'живёт в Кракове')[0]?.fact).toBe('живёт в Кракове');
    expect(memoryOps.remove(one, 0)).toEqual([]);
  });

  it('profile patch sends only changed fields, empty phone as null', () => {
    const initial = { name: 'Дима', phone: '+380', timezone: '', notes: '' };
    expect(profilePatch({ ...initial, phone: ' ', notes: 'любит кофе' }, initial)).toEqual({ phone: null, notes: 'любит кофе' });
  });
});
