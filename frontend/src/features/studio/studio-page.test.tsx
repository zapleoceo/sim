import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { StudioState } from '@/lib/api/studio';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp } from '@/test/render-app';
import { STUDIO_LIMIT_ERROR, withinStudioLimit } from './studio-page';

const READY: StudioState = {
  photo_url: null,
  video_url: null,
  models: ['sub-model'],
  text_available: true,
  media_available: false,
  detail: 'Видео генерируется по запросу.',
};

describe('studio', () => {
  it('enforces the 24 messages / 32 000 characters limit', () => {
    const msg = (content: string) => ({ role: 'user' as const, content });
    expect(withinStudioLimit(Array.from({ length: 24 }, () => msg('a')))).toBe(true);
    expect(withinStudioLimit(Array.from({ length: 25 }, () => msg('a')))).toBe(false);
    expect(withinStudioLimit([msg('я'.repeat(32001))])).toBe(false);
  });

  it('shows honest empty media states and chats with the subscription model', async () => {
    const log = fakeApi({
      'GET /api/studio': READY,
      'POST /api/studio/chat': { text: 'Привет, я тут', model: 'sub-model' },
    });
    await renderApp('/studio');
    expect(await screen.findByText(/Исходное фото пока не подключено/)).toBeInTheDocument();
    expect(screen.getByText(/Видео ещё не сгенерировано/)).toBeInTheDocument();
    expect(screen.getByText('Видео генерируется по запросу.')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('Сообщение'), 'Привет');
    await userEvent.click(screen.getByRole('button', { name: /Отправить/ }));
    expect(await screen.findByText('Привет, я тут')).toBeInTheDocument();
    const chat = log.find((r) => r.method === 'POST');
    expect(chat?.body).toEqual({ model: 'sub-model', messages: [{ role: 'user', content: 'Привет' }] });
    expect(screen.getByLabelText('Сообщение')).toHaveValue('');

    await userEvent.click(screen.getByRole('button', { name: 'Новый разговор' }));
    expect(screen.queryByText('Привет, я тут')).not.toBeInTheDocument();
  });

  it('keeps the draft and shows the error when the chat fails', async () => {
    fakeApi({ 'GET /api/studio': READY, 'POST /api/studio/chat': reply(503) });
    await renderApp('/studio');
    await userEvent.type(await screen.findByLabelText('Сообщение'), 'Привет');
    await userEvent.click(screen.getByRole('button', { name: /Отправить/ }));
    expect(await screen.findByText('Сервис временно недоступен. Попробуйте позже')).toBeInTheDocument();
    expect(screen.getByLabelText('Сообщение')).toHaveValue('Привет');
  });

  it('disables the chat when the text model is unavailable', async () => {
    fakeApi({ 'GET /api/studio': { ...READY, text_available: false } });
    await renderApp('/studio');
    expect(await screen.findByText(/Текстовая модель пока недоступна/)).toBeInTheDocument();
    expect(screen.getByLabelText('Сообщение')).toBeDisabled();
  });

  it('offers a retry when the studio does not load', async () => {
    let calls = 0;
    fakeApi({ 'GET /api/studio': () => (++calls === 1 ? reply(500) : READY) });
    await renderApp('/studio');
    await userEvent.click(await screen.findByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByText(/Исходное фото пока не подключено/)).toBeInTheDocument());
  });

  it('warns about the limit before calling the model', () => {
    expect(STUDIO_LIMIT_ERROR).toMatch(/24 сообщения или 32 000 символов/);
  });
});
