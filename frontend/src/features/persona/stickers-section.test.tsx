import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { describe, expect, it, vi } from 'vitest';
import type { Sticker } from '@/lib/types';
import { fakeApi, reply } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { PersonaIdProvider } from './persona-scope';
import { StickersSection } from './stickers-section';

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), info: vi.fn() }) }));

function sticker(id: number, patch: Partial<Sticker> = {}): Sticker {
  return { id, channel_id: 4, emoji: '🐱', set_name: 'cats', set_title: 'Котики', faved: false, ...patch };
}

const STICKERS = [sticker(1), sticker(2, { emoji: '😺' }), sticker(3, { emoji: '😘', faved: true })];

async function render(refetchAfterMs?: number): Promise<void> {
  await renderWithApp(
    <PersonaIdProvider id={2}>
      <StickersSection refetchAfterMs={refetchAfterMs} />
    </PersonaIdProvider>,
  );
}

describe('StickersSection', () => {
  it('no stickers: says how to add them', async () => {
    fakeApi({ 'GET /api/personas/2/stickers': { items: [] } });
    await render();
    expect(await screen.findByText(/У её аккаунта Telegram нет стикеров\. Установите наборы/)).toBeInTheDocument();
    expect(screen.getByText(/Потом нажмите «Обновить»\./)).toBeInTheDocument();
    expect(screen.queryByText(/Стикеров:/)).not.toBeInTheDocument();
  });

  it('counts her stickers and groups their emojis: «Избранное» first, then by set', async () => {
    fakeApi({ 'GET /api/personas/2/stickers': { items: STICKERS } });
    await render();
    expect(await screen.findByText('Стикеров: 3')).toBeInTheDocument();
    const groups = screen.getAllByRole('region').filter((r) => r.id !== 'stickers');
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual(['Избранное', 'Котики']);
    expect(within(screen.getByRole('region', { name: 'Избранное' })).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['😘']);
    expect(within(screen.getByRole('region', { name: 'Котики' })).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['🐱', '😺']);
  });

  it('«Обновить» queues a re-read, says so and reads the list again a few seconds later', async () => {
    let gets = 0;
    const log = fakeApi({
      'GET /api/personas/2/stickers': () => {
        gets += 1;
        return { items: gets >= 3 ? [...STICKERS, sticker(4, { emoji: '🦊', set_name: 'fox', set_title: 'Лиса' })] : STICKERS };
      },
      'POST /api/personas/2/stickers/sync': reply(202, { channels: [4] }),
    });
    await render(10);
    await screen.findByText('Стикеров: 3');
    await userEvent.click(screen.getByRole('button', { name: 'Обновить' }));

    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('Обновляю стикеры из Telegram…'));
    expect(log.filter((r) => r.method === 'POST').map((r) => r.path)).toEqual(['/api/personas/2/stickers/sync']);
    expect(await screen.findByText('🦊')).toBeInTheDocument();             // the delayed re-read
    expect(screen.getByText('Стикеров: 4')).toBeInTheDocument();
    expect(gets).toBeGreaterThanOrEqual(3);
  });

  it('a failed load offers a retry', async () => {
    fakeApi({ 'GET /api/personas/2/stickers': reply(500, { error: 'упало' }) });
    await render();
    expect(await screen.findByText('Стикеры не загрузились')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });
});
