import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import type { StudioDraft } from '@/lib/api/studio';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp, testUser } from '@/test/render-app';

const path = '/api/studio/media/draft/video';
const state = { photo_url: null, video_url: null, models: [], text_available: false,
  media_available: false, personal_access: true, draft_scope: 'a'.repeat(64), detail: '' };

it('saves and restores a video draft while disabled; repeated save, cancel and deletion never generate', async () => {
  let saved: StudioDraft = { scope: state.draft_scope, revision: 0, text: '' };
  const log = fakeApi({ 'GET /api/studio': state, 'GET /api/studio/media': { operations: [] },
    [`GET ${path}`]: () => saved,
    [`PUT ${path}`]: (request) => { saved = { ...(request.body as StudioDraft), revision: saved.revision + 1 }; return saved; },
  });
  const first = await renderApp('/studio');
  const input = await screen.findByLabelText('Текст черновика кружочка');
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.type(input, 'Synthetic video');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
  expect(await screen.findByText('Черновик сохранён. Генерация не запускалась.')).toBeInTheDocument();
  first.unmount();
  await renderApp('/studio');
  await waitFor(() => expect(screen.getByLabelText('Текст черновика кружочка')).toHaveValue('Synthetic video'));
  await userEvent.type(screen.getByLabelText('Текст черновика кружочка'), ' revised');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
  await waitFor(() => expect(saved.revision).toBe(2));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  await userEvent.type(screen.getByLabelText('Текст черновика кружочка'), ' unsaved');
  await userEvent.click(screen.getByRole('button', { name: 'Отменить изменения' }));
  expect(screen.getByLabelText('Текст черновика кружочка')).toHaveValue('Synthetic video revised');
  await userEvent.click(screen.getByRole('button', { name: 'Удалить черновик' }));
  await waitFor(() => expect(saved.text).toBe(''));
  expect(log.filter((request) => request.method === 'POST')).toHaveLength(0);
});

it('keeps text on storage failure and does not leak a draft to a different user/reference', async () => {
  const log = fakeApi({ 'GET /api/studio': state, 'GET /api/studio/media': { operations: [] },
    [`GET ${path}`]: { scope: state.draft_scope, revision: 1, text: 'Private saved' },
    [`PUT ${path}`]: reply(503),
  });
  const first = await renderApp('/studio');
  await waitFor(() => expect(screen.getByLabelText('Текст черновика кружочка')).toHaveValue('Private saved'));
  await userEvent.type(screen.getByLabelText('Текст черновика кружочка'), ' edit');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
  expect(await screen.findByText(/Сохранить не удалось/)).toBeInTheDocument();
  expect(screen.getByLabelText('Текст черновика кружочка')).toHaveValue('Private saved edit');
  first.unmount();
  fakeApi({ 'GET /api/studio': { ...state, personal_access: false, draft_scope: null } });
  const second = await renderApp('/studio', { user: testUser('owner', { id: 2 }) });
  expect(screen.queryByLabelText('Текст черновика кружочка')).not.toBeInTheDocument();
  second.unmount();
  const scope = 'b'.repeat(64);
  fakeApi({ 'GET /api/studio': { ...state, draft_scope: scope }, 'GET /api/studio/media': { operations: [] },
    [`GET ${path}`]: { scope, revision: 0, text: '' } });
  await renderApp('/studio');
  await waitFor(() => expect(screen.getByLabelText('Текст черновика кружочка')).toBeEnabled());
  expect(screen.getByLabelText('Текст черновика кружочка')).toHaveValue('');
  expect(log.filter((request) => request.method === 'POST')).toHaveLength(0);
});
