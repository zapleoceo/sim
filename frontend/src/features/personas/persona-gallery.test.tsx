import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EMPTY_PERSONA } from './persona-schema';
import type { Persona, PersonaMedia } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { renderApp } from '@/test/render-app';

const MARINA: Persona = {
  ...EMPTY_PERSONA,
  id: 1,
  name: 'Марина',
  description: 'Тёплая',
  created_at: '2026-11-01T10:00:00Z',
};

const PHOTO: PersonaMedia = {
  id: 10,
  persona_id: 1,
  kind: 'photo',
  storage_name: 'abc.jpg',
  caption: 'аватар',
  tags: ['selfie'],
  file_url: '/api/media/abc.jpg',
  created_at: '2026-11-01T10:00:00Z',
};

function backend(media: PersonaMedia[] = []) {
  return fakeApi({
    'GET /api/personas': { items: [MARINA] },
    'GET /api/strategies': { items: [] },
    'GET /api/models': { items: [] },
    'GET /api/personas/1/media': { items: media },
    'GET /api/personas/1/prompt': { system: 'test', fixed: '' },
  });
}

describe('persona gallery', () => {
  it('shows gallery section on persona page', async () => {
    backend();
    await renderApp('/personas/1');
    expect(await screen.findByText('Галерея')).toBeInTheDocument();
    expect(await screen.findByText('Пока пусто')).toBeInTheDocument();
  });

  it('lists media items with filter buttons', async () => {
    backend([PHOTO]);
    await renderApp('/personas/1');
    expect(await screen.findByText('Галерея')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByAltText('аватар')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Все' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Фото' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Видео' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Кружочки' })).toBeInTheDocument();
  });

  it('filters media by kind', async () => {
    const video: PersonaMedia = { ...PHOTO, id: 11, kind: 'video', storage_name: 'v.mp4', caption: 'ролик', file_url: '/api/media/v.mp4' };
    backend([PHOTO, video]);
    await renderApp('/personas/1');
    await waitFor(() => expect(screen.getByAltText('аватар')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Фото' }));
    expect(screen.getByAltText('аватар')).toBeInTheDocument();
    expect(screen.queryByText('ролик')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Все' }));
    expect(screen.getByAltText('аватар')).toBeInTheDocument();
    expect(screen.getByText('ролик')).toBeInTheDocument();
  });
});
