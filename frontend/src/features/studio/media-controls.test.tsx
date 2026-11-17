import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp } from '@/test/render-app';

const state = { photo_url: null, video_url: null, models: [], text_available: false,
  media_available: false, detail: '', speech_available: false, video_available: false,
  capabilities_verified: false };

it('keeps media disabled without claiming a verified provider outage', async () => {
  const log = fakeApi({ 'GET /api/studio': state });
  await renderApp('/studio');
  expect(await screen.findByRole('button', { name: 'Создать медиа' })).toBeDisabled();
  expect(screen.getByLabelText('Текст для генерации')).toBeDisabled();
  expect(screen.getByText(/Подписочный чат не означает доступ/)).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Тип результата'), 'video');
  expect(screen.getByRole('button', { name: 'Создать медиа' })).toBeDisabled();
  expect(log.filter((request) => request.method === 'POST')).toHaveLength(0);
});

it('only reads status after an uncertain POST; keeps the draft and prevents another POST', async () => {
  const log = fakeApi({
    'GET /api/studio': { ...state, speech_available: true, capabilities_verified: true },
    'POST /api/studio/media': reply(502),
  });
  await renderApp('/studio');
  await userEvent.type(await screen.findByLabelText('Текст для генерации'), 'Synthetic');
  await userEvent.click(screen.getByRole('button', { name: 'Создать медиа' }));
  expect(await screen.findByText(/Результат не подтверждён/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Создать медиа' })).toBeDisabled();
  expect(screen.getByLabelText('Текст для генерации')).toHaveValue('Synthetic');
  await userEvent.click(screen.getByRole('button', { name: 'Проверить статус' }));
  await waitFor(() => expect(screen.getByText(/Статус получить не удалось/)).toBeInTheDocument());
  expect(log.filter((request) => request.method === 'POST')).toHaveLength(1);
});
