import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Channel, Persona, PersonaRow } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { CloneDialog } from './clone-dialog';
import { CLONE_PARTS, cloneNameError, defaultParts, orderedParts } from './clone-logic';
import { PersonaHeader } from './persona-page';
import { PersonasPage, channelSummary } from './personas-page';

const channel = (patch: Partial<Channel>): Channel => ({
  id: 1, persona_id: 1, kind: 'telegram_user', title: 'Марина', status: 'connected', account_label: '@marina',
  last_error: null, last_activity_at: null, ...patch,
});

const ROWS: PersonaRow[] = [
  { id: 1, name: 'Марина', avatar_media_id: null, is_active: true, ai_enabled: true, leads_count: 12, channels: [channel({})] },
  { id: 2, name: 'Оля', avatar_media_id: null, is_active: true, ai_enabled: false, leads_count: 0, channels: [] },
  { id: 3, name: 'Старая', avatar_media_id: null, is_active: false, ai_enabled: true, leads_count: 3, channels: [] },
];

describe('PersonasPage', () => {
  it('lists personas with channel, leads and AI state, links to each page', async () => {
    fakeApi({ 'GET /api/personas': { items: ROWS } });
    await renderWithApp(<PersonasPage />);
    const table = await screen.findByRole('table');
    await within(table).findByText('@marina · подключён');
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(within(table).getByRole('link', { name: /Марина/ })).toHaveAttribute('href', '/personas/1');
    expect(within(table).getByRole('link', { name: '12' })).toHaveAttribute('href', '/leads?persona=1');
    expect(within(table).getAllByText('нет канала')).toHaveLength(2);
    expect(within(table).getByText('в архиве')).toBeInTheDocument();
    expect(within(table).getAllByText('выключен')).toHaveLength(2);       // Оля (AI off), archived
    expect(within(table).getByRole('button', { name: 'Клонировать Оля' })).toBeInTheDocument();
  });

  it('opens the clone dialog from a row', async () => {
    fakeApi({ 'GET /api/personas': { items: ROWS } });
    await renderWithApp(<PersonasPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Клонировать Марина' }));
    expect(await screen.findByRole('dialog', { name: 'Клонировать «Марина»' })).toBeInTheDocument();
  });

  it('channel summary prefers the connected account', () => {
    expect(channelSummary({ ...ROWS[1], channels: [] })).toEqual({ label: 'нет канала', variant: 'warning' });
    const row = { ...ROWS[0], channels: [channel({ id: 5, status: 'error', account_label: '' }), channel({})] };
    expect(channelSummary(row).label).toBe('@marina · подключён');
  });
});

describe('CloneDialog', () => {
  const persona = { id: 1, name: 'Марина' } as Persona;

  it('ticks everything but the canon and sends the parts in page order', async () => {
    const onCloned = vi.fn();
    const log = fakeApi({ 'POST /api/personas/1/clone': (req) => ({ id: 9, name: (req.body as { name: string }).name }) });
    await renderWithApp(<CloneDialog source={persona} takenNames={['Марина']} open onOpenChange={() => undefined} onCloned={onCloned} />);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('checkbox', { name: /Канон/ })).not.toBeChecked();
    expect(within(dialog).getByRole('checkbox', { name: /Медиатека/ })).toBeChecked();
    expect(within(dialog).getByText(/файлы копируются/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /Канон/ }));
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /Промт/ }));

    await userEvent.click(within(dialog).getByRole('button', { name: 'Клонировать' }));
    expect(await within(dialog).findByText('Укажите имя')).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText('Имя нового персонажа'), 'марина');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Клонировать' }));
    expect(await within(dialog).findByText('Персонаж с таким именем уже есть')).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'POST')).toHaveLength(0);

    await userEvent.clear(within(dialog).getByLabelText('Имя нового персонажа'));
    await userEvent.type(within(dialog).getByLabelText('Имя нового персонажа'), '  Оля  ');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Клонировать' }));
    await waitFor(() => expect(onCloned).toHaveBeenCalledWith(expect.objectContaining({ id: 9 })));
    expect(log.find((r) => r.method === 'POST')?.body).toEqual({
      name: 'Оля',
      parts: ['basics', 'agents', 'voice', 'guard', 'appearance', 'world', 'arcs', 'media', 'switches', 'canon'],
    });
  });

  it('from the persona page header checks the name against every persona, like the list', async () => {
    const log = fakeApi({ 'GET /api/personas': { items: ROWS } });
    const marina = { ...ROWS[0], gender: 'female', language: 'ru', timezone: 'Europe/Kyiv', birth_date: null, prompt: '',
      appearance: '', voice_ambience_enabled: false, voice_id: '', voice_style: '', voice_enabled: false, photo_enabled: false,
      video_enabled: false, calls_enabled: false, reply_delay_s: 8 } as Persona;
    await renderWithApp(<PersonaHeader persona={marina} />);
    await waitFor(() => expect(log.some((r) => r.path === '/api/personas')).toBe(true));
    await userEvent.click(screen.getByRole('button', { name: 'Клонировать' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('Имя нового персонажа'), 'оля');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Клонировать' }));
    expect(await within(dialog).findByText('Персонаж с таким именем уже есть')).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'POST')).toHaveLength(0);
  });

  it('logic: defaults, order and name checks', () => {
    expect(CLONE_PARTS).toHaveLength(11);
    expect(defaultParts().has('canon')).toBe(false);
    expect(orderedParts(new Set(['media', 'basics']))).toEqual(['basics', 'media']);
    expect(cloneNameError(' ', [])).toBe('Укажите имя');
    expect(cloneNameError('x'.repeat(121), [])).toBe('Не длиннее 120 символов');
    expect(cloneNameError('Оля', ['Марина'])).toBeNull();
  });
});
