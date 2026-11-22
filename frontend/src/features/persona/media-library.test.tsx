import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Media } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { MediaLibrarySection } from './media-library';
import { PersonaIdProvider } from './persona-scope';

function media(id: number, patch: Partial<Media> = {}): Media {
  return {
    id, kind: 'photo', mime: 'image/png', url: `/api/media/${id}/file`, caption: `фото ${id}`, tags: [], description: '',
    source: 'generated', is_reference: false, width: null, height: null, duration_s: null, file_name: '',
    created_at: '2026-11-22T15:34:18Z', scene: null, ...patch,
  };
}

const KITCHEN = media(26, { caption: 'На кухне', scene: { room: 'кухня', place: 'дом, кухня', outfit: 'свитер' } });
const VIDEO = media(27, { kind: 'video', mime: 'video/mp4', caption: 'Кружок', duration_s: 8 });
const UPLOADED = media(1, { source: 'library', caption: 'Портрет', is_reference: true });

async function show(): Promise<ReturnType<typeof fakeApi>> {
  const log = fakeApi({
    'GET /api/personas/1/media': { items: [KITCHEN, VIDEO, UPLOADED] },
    'PATCH /api/media/26': (req) => ({ ...KITCHEN, scene: { ...KITCHEN.scene, room_ref: (req.body as { is_room_ref: boolean }).is_room_ref } }),
  });
  await renderWithApp(<PersonaIdProvider id={1}><MediaLibrarySection /></PersonaIdProvider>);
  await screen.findByRole('button', { name: 'Открыть: На кухне' });
  return log;
}

describe('media library', () => {
  it('a click on a tile opens the full-screen viewer at that file', async () => {
    await show();
    expect(screen.getByText('Файлов: 3')).toBeInTheDocument();
    expect(screen.getByText('0:08')).toBeInTheDocument();                  // the video tile shows its length
    await userEvent.click(screen.getByRole('button', { name: 'Открыть: Кружок' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelector('h2')?.textContent).toBe('Видео 2 из 3: Кружок');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Следующее' }));
    expect(dialog.querySelector('h2')?.textContent).toBe('Фото 3 из 3: Портрет');
    expect(within(dialog).getByText('Загружено в медиатеку')).toBeInTheDocument();
  });

  it('pins a generated photo as the reference of its room', async () => {
    const log = await show();
    await userEvent.click(screen.getByRole('button', { name: 'Открыть: На кухне' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('switch', { name: 'Эталон комнаты «кухня»' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PATCH')).toHaveLength(1));
    const patch = log.find((r) => r.method === 'PATCH');
    expect(patch?.path).toBe('/api/media/26');
    expect(patch?.body).toEqual({ is_room_ref: true });
  });

  it('a room reference only for a generated photo of her room', async () => {
    await show();
    expect(screen.getAllByRole('switch', { name: /Эталон комнаты/ })).toHaveLength(1);
    // Face references: photos only, not the video.
    expect(screen.getAllByRole('switch', { name: 'Референс внешности' })).toHaveLength(2);
  });
});
