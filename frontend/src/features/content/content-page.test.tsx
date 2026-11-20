import { vi } from 'vitest';
import { act, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authStore } from '@/lib/auth';
import type { ContentPost, Persona } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { CSRF, renderApp, screen, testUser } from '@/test/render-app';

const persona = {
  id: 3, name: 'Марина', gender: 'female', avatar_url: '', agent_prompt: 'Ты Марина.', language: 'ru',
  model: '', voice_id: '', voice_mode: 'off', voice_style: '', voice_ambience: 'off', accept_calls: false, strategy_id: null, is_active: true, created_at: '2026-01-01T00:00:00Z', birth_date: null, timezone: 'Europe/Kyiv', routine: '',
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

  it('queues a caption once and preserves edits until the suggestion is applied', async () => {
    const id = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
    const path = `/api/content/1/caption-operations/${id}`;
    let finish: (result: object) => void = () => undefined;
    const result = new Promise<object>((resolve) => { finish = resolve; });
    const log = fakeApi({
      ...base, 'GET /api/content': { items: [draft] }, 'PUT /api/content/1': draft,
      [`POST ${path}`]: reply(504), [`GET ${path}`]: () => result,
    });
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1795171200000);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue('9d80c89d-438e-4eef-8848-7bfb39a89af3');
    try {
      const user = userEvent.setup();
      await renderApp('/content');
      await user.click(await screen.findByRole('button', { name: /Осенний образ/ }));
      await user.click(screen.getByRole('button', { name: 'Сгенерировать подпись' }));
      await waitFor(() => expect(log.some((entry) => entry.method === 'GET' && entry.path === path)).toBe(true));
      const input = screen.getByLabelText('Подпись');
      await user.clear(input);
      await user.type(input, 'Ручная подпись');
      finish({ id: '1795171200000-d7dd86f1-1943-4fcb-bc89-8c839db95995', status: 'complete', result: { caption: 'Предложение модели' } });
      expect(await screen.findByText('Предложение модели')).toBeVisible();
      expect(input).toHaveValue('Ручная подпись');
      await user.click(screen.getByRole('button', { name: 'Вставить подпись' }));
      expect(input).toHaveValue('Предложение модели');
      expect(log.filter(({ method, path: url }) => method === 'POST' && url === path)).toHaveLength(1);
      expect(log.filter(({ method }) => method === 'PUT')).toHaveLength(1);
    } finally {
      clock.mockRestore();
      uuid.mockRestore();
    }
  });

  it('does not start generation when the account changes while saving', async () => {
    let finish: (result: ContentPost) => void = () => undefined;
    const saved = new Promise<ContentPost>((resolve) => { finish = resolve; });
    const log = fakeApi({ ...base, 'GET /api/content': { items: [draft] },
      'PUT /api/content/1': () => saved });
    const user = userEvent.setup();
    await renderApp('/content');
    await user.click(await screen.findByRole('button', { name: /Осенний образ/ }));
    await user.click(screen.getByRole('button', { name: 'Сгенерировать подпись' }));
    await waitFor(() => expect(log.some(({ method }) => method === 'PUT')).toBe(true));
    await act(async () => {
      authStore.apply({ user: testUser('owner', { id: 2 }), csrf_token: CSRF });
      finish(draft);
      await saved;
    });
    expect(log.some(({ method, path }) => method === 'POST' && path.includes('caption-operations'))).toBe(false);
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
