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
    'GET /api/studio': { ...state, personal_access: true, media_available: true,
      speech_available: true, capabilities_verified: true },
    'GET /api/studio/media': { operations: [] },
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

it('offers only the configured kind and waits for an explicit submit', async () => {
  const log = fakeApi({
    'GET /api/studio': { ...state, personal_access: true, media_available: true,
      video_available: true, capabilities_verified: true },
    'GET /api/studio/media': { operations: [] },
  });
  await renderApp('/studio');
  const button = await screen.findByRole('button', { name: 'Создать медиа' });
  expect(button).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText('Тип результата'), 'video');
  await userEvent.type(screen.getByLabelText('Текст для генерации'), 'Synthetic');
  expect(button).toBeEnabled();
  expect(log.filter((request) => request.method === 'POST')).toHaveLength(0);
  await userEvent.selectOptions(screen.getByLabelText('Тип результата'), 'speech');
  expect(button).toBeDisabled();
});

it('restores an unknown operation after remount without another submission or automatic reconciliation', async () => {
  const id = '00000000-0000-4000-8000-000000000001';
  const operation = { operation_id: id, kind: 'video', status: 'unknown', artifact_url: null };
  const log = fakeApi({
    'GET /api/studio': { ...state, personal_access: true },
    'GET /api/studio/media': { operations: [operation] },
    [`GET /api/studio/media/${id}`]: operation,
  });
  const first = await renderApp('/studio');
  expect(await screen.findByText(/Восстановлена незавершённая операция/)).toBeInTheDocument();
  first.unmount();
  await renderApp('/studio');
  expect(await screen.findByText(`Операция: ${id}`)).toBeInTheDocument();
  expect(screen.getByLabelText('Тип результата')).toHaveValue('video');
  expect(screen.getByRole('button', { name: 'Создать медиа' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Сверить с Broker' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Проверить статус' }));
  expect(log.filter((request) => request.method === 'POST')).toHaveLength(0);
});

it('updates the history status when the restored operation completes', async () => {
  const id = '00000000-0000-4000-8000-000000000002';
  const operation = { operation_id: id, kind: 'video', status: 'unknown', artifact_url: null };
  fakeApi({
    'GET /api/studio': { ...state, personal_access: true },
    'GET /api/studio/media': { operations: [operation] },
    [`GET /api/studio/media/${id}`]: { ...operation, status: 'complete' },
  });
  await renderApp('/studio');
  await userEvent.click(await screen.findByRole('button', { name: 'Проверить статус' }));
  expect(await screen.findByText('Результат сохранён.')).toBeInTheDocument();
  expect(screen.getByText(/Видео · Готово/)).toBeInTheDocument();
  expect(screen.queryByText(/Видео · Исход неизвестен/)).not.toBeInTheDocument();
});
