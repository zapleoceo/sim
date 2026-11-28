import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ClientMatch, ClientTimelineItem, MatchClient } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { ClientFeed } from './client-feed';
import { ClientMatches, MatchesPanel } from './client-matches';

const client = (id: number, name: string, messengers: string[]): MatchClient => ({
  id, name, username: null, ai_enabled: true, phone: '+380994811889', messengers,
  persona: { id: 1, name: 'Марина', avatar_media_id: null }, last_seen_at: null,
});
const DIMA = client(1, 'Дима', ['telegram_user']);
const WHATSAPP_DIMA = client(7, 'Zaporozhets Dmitry', ['whatsapp']);
const PAIR: ClientMatch = { clients: [DIMA, WHATSAPP_DIMA], reasons: [{ kind: 'phone', text: 'один телефон …994811889' }] };

describe('«Возможно, один человек» in the lead card', () => {
  it('shows the other client with the reason and merges it into this card after a confirmation', async () => {
    const log = fakeApi({
      'GET /api/clients/1/matches': { items: [PAIR] },
      'POST /api/clients/1/merge': { ...DIMA, messengers: ['telegram_user', 'whatsapp'], chats: [] },
    });
    await renderWithApp(<ClientMatches client={DIMA} />);
    const section = await screen.findByRole('region', { name: 'Возможно, это один человек' });
    expect(within(section).getByRole('link', { name: 'Zaporozhets Dmitry' })).toBeInTheDocument();
    expect(within(section).getByText('один телефон …994811889')).toBeInTheDocument();
    expect(within(section).getByRole('img', { name: 'WhatsApp' })).toBeInTheDocument();
    expect(within(section).queryByRole('link', { name: 'Дима' })).not.toBeInTheDocument();
    // The card's column is narrow: the row stays a column (who, why, buttons) at any width.
    expect(within(section).getAllByRole('listitem')[0].className).not.toContain('flex-row');

    await userEvent.click(within(section).getByRole('button', { name: 'Объединить' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Объединить «Zaporozhets Dmitry» с «Дима»?' });
    expect(dialog).toHaveTextContent('Разделить обратно нельзя');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Объединить' }));
    await waitFor(() => expect(log.some((r) => r.method === 'POST' && r.path === '/api/clients/1/merge')).toBe(true));
    expect(log.find((r) => r.path === '/api/clients/1/merge')?.body).toEqual({ other_id: 7 });
  });

  it('does nothing without a confirmation and shows nothing without suggestions', async () => {
    const log = fakeApi({ 'GET /api/clients/1/matches': { items: [PAIR] } });
    const { unmount } = await renderWithApp(<ClientMatches client={DIMA} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Объединить' }));
    const dialog = await screen.findByRole('alertdialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    expect(log.some((r) => r.method === 'POST')).toBe(false);
    unmount();

    const empty = fakeApi({ 'GET /api/clients/1/matches': { items: [] } });
    await renderWithApp(<ClientMatches client={DIMA} />);
    await waitFor(() => expect(empty.some((r) => r.path === '/api/clients/1/matches')).toBe(true));
    expect(screen.queryByRole('region', { name: 'Возможно, это один человек' })).not.toBeInTheDocument();
  });
});

describe('«Возможно, один человек» on the leads page', () => {
  it('lists both sides of every pair and marks a pair as different people', async () => {
    const log = fakeApi({
      'GET /api/clients/matches': { items: [PAIR] },
      'POST /api/clients/1/distinct': null,
    });
    await renderWithApp(<MatchesPanel />);
    const panel = await screen.findByRole('region', { name: 'Возможно, один человек: 1' });
    expect(within(panel).getByRole('link', { name: 'Дима' })).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: 'Zaporozhets Dmitry' })).toBeInTheDocument();
    expect(within(panel).getAllByRole('listitem')[0].className).toContain('xl:flex-row');
    await userEvent.click(within(panel).getByRole('button', { name: 'Разные люди' }));
    await waitFor(() => expect(log.find((r) => r.path === '/api/clients/1/distinct')?.body).toEqual({ other_id: 7 }));
  });
});

const item = (id: number, kind: string, text: string, createdAt: string): ClientTimelineItem => ({
  id, kind: 'message_in', actor: 'client', created_at: createdAt, in_context: true, reply_run_id: null, data: {},
  message: { id, direction: 'in', author: 'client', kind: 'text', text, status: 'received', error: null, sent_at: createdAt },
  chat_id: kind === 'whatsapp' ? 7 : 1, channel: { id: kind === 'whatsapp' ? 4 : 3, kind, title: '' },
});

describe('«Лента» of the lead card', () => {
  it('joins every messenger in one stream and names the messenger where it changes', async () => {
    fakeApi({
      'GET /api/clients/1/timeline': {
        items: [
          item(30, 'whatsapp', 'и в ватсапе', '2026-11-28T11:00:00Z'),
          item(20, 'telegram_user', 'привет из телеги', '2026-11-28T10:00:00Z'),
          item(10, 'telegram_user', 'утро', '2026-11-28T09:00:00Z'),
        ],
      },
    });
    await renderWithApp(<ClientFeed clientId={1} />);
    const log = await screen.findByRole('log', { name: 'Лента по всем мессенджерам' });
    expect(within(log).getByText('и в ватсапе')).toBeInTheDocument();
    expect(within(log).getByText('привет из телеги')).toBeInTheDocument();
    // Telegram once (two messages in a row), WhatsApp once.
    expect(within(log).getAllByText('Telegram')).toHaveLength(1);
    expect(within(log).getAllByText('WhatsApp')).toHaveLength(1);
  });
});
