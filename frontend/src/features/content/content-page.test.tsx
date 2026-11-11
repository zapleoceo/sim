import { waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ContentPost, Persona } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { renderApp, screen } from '@/test/render-app';

const persona = {
  id: 3, name: 'Марина', age: null, gender: 'female', avatar_url: '', description: 'd', prohibitions: '', language: 'ru',
  model: '', voice_id: '', voice_mode: 'off', voice_style: '', strategy_id: null, is_active: true, created_at: '2026-01-01T00:00:00Z',
} satisfies Persona;
const draft: ContentPost = {
  id: 1, persona_id: 3, channel_id: null, kind: 'photo', caption: 'Осенний образ', media_urls: [],
  status: 'draft', scheduled_at: null, published_at: null, error: null,
};

const base = {
  'GET /api/personas': { items: [persona] },
  'GET /api/channels': { items: [] },
};

describe('ContentPage', () => {
  it('shows the empty state without posts', async () => {
    fakeApi({ ...base, 'GET /api/content': { items: [] } });
    await renderApp('/content');
    expect(await screen.findByText('Постов пока нет')).toBeInTheDocument();
  });

  it('approves a draft and moves it to the approved tab', async () => {
    const log = fakeApi({
      ...base,
      'GET /api/content': { items: [draft] },
      'POST /api/content/1/approve': { ...draft, status: 'approved' },
    });
    const user = userEvent.setup();
    await renderApp('/content');

    await user.click(await screen.findByRole('button', { name: /Осенний образ/ }));
    const editor = await screen.findByRole('form', { name: 'Редактирование поста' });
    expect(within(editor).getByText(/Статус: Черновики/)).toBeInTheDocument();
    await user.click(within(editor).getByRole('button', { name: 'Одобрить' }));

    await waitFor(() => expect(log.some((r) => r.path === '/api/content/1/approve')).toBe(true));
    expect(await within(editor).findByText(/Статус: Одобрено/)).toBeInTheDocument();
    expect(within(editor).queryByRole('button', { name: 'Одобрить' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Одобрено/ })).toHaveTextContent('1');
  });

  it('validates media URLs and creates a new post', async () => {
    const log = fakeApi({
      ...base,
      'GET /api/content': { items: [] },
      'POST /api/content': (req: { body: unknown }) => ({ ...draft, id: 2, ...(req.body as object), status: 'draft' }),
    });
    const user = userEvent.setup();
    await renderApp('/content');

    await user.click(await screen.findByRole('button', { name: 'Новый пост' }));
    const editor = await screen.findByRole('form', { name: 'Новый пост' });
    await user.type(within(editor).getByLabelText('Подпись'), 'Привет');
    const media = within(editor).getByLabelText('Ссылки на медиа (по одной в строке)');
    await user.type(media, 'photo.jpg');
    await user.click(within(editor).getByRole('button', { name: 'Сохранить' }));
    expect(await within(editor).findByText('Каждая строка должна быть ссылкой https://…')).toBeInTheDocument();
    expect(log.some((r) => r.method === 'POST')).toBe(false);

    await user.clear(media);
    await user.type(media, 'https://cdn.example.com/a.jpg');
    await user.click(within(editor).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(log.some((r) => r.method === 'POST' && r.path === '/api/content')).toBe(true));
    expect(log.find((r) => r.method === 'POST')!.body).toEqual({
      persona_id: 3, channel_id: null, kind: 'photo', caption: 'Привет', media_urls: ['https://cdn.example.com/a.jpg'], scheduled_at: null,
    });
  });
});
