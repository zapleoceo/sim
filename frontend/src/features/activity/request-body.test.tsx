import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { payloadText, showsRequestBody } from './log-logic';
import { RequestBody } from './trace-panel';

it('shows the kept body, the truncation note and copies exactly what the server returned', async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  const content = { chat: 55, text: 'Привет', token: '[REDACTED]' };
  render(<RequestBody body={{ content, truncated: true, redacted: true }} />);
  await user.click(screen.getByText('Тело запроса'));
  expect(screen.getByText('Данные усечены по лимиту хранения.')).toBeVisible();
  expect(screen.getByText(/"text": "Привет"/)).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Копировать тело' }));
  expect(JSON.parse(copy.mock.calls[0][0])).toEqual(content);
  expect(await screen.findByRole('status')).toHaveTextContent('Тело скопировано');
});

it('says why a body was not kept, and that old rows have none', async () => {
  const user = userEvent.setup();
  const { unmount } = render(<RequestBody body={{ omitted: 'multipart/form-data, 2048 байт — тело не сохраняется' }} />);
  await user.click(screen.getByText('Тело запроса'));
  expect(screen.getByText('Тело не сохранено: multipart/form-data, 2048 байт — тело не сохраняется')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Копировать тело' })).toBeNull();
  unmount();
  render(<RequestBody body={null} />);
  await user.click(screen.getByText('Тело запроса'));
  expect(screen.getByText('Тело не сохранено')).toBeVisible();
});

it('offers the block only for request steps that can carry a body', () => {
  expect(showsRequestBody({ kind: 'http', name: 'POST /api/content', request_body: null })).toBe(true);
  expect(showsRequestBody({ kind: 'http', name: 'GET /api/clients', request_body: null })).toBe(false);
  expect(showsRequestBody({ kind: 'webhook', name: 'GET /hooks/x', request_body: null })).toBe(true);
  expect(showsRequestBody({ kind: 'llm', name: 'POST x', request_body: null })).toBe(false);
  expect(payloadText('a=1')).toBe('a=1');
  expect(payloadText({ a: 1 })).toBe('{\n  "a": 1\n}');
});
