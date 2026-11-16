import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrokerExchange } from './trace-panel';

it('labels historical absence and never fabricates an actual response', async () => {
  render(<BrokerExchange exchange={null} />);
  await userEvent.click(screen.getByText('Запрос брокера'));
  expect(screen.getByText('Полный запрос не сохранён')).toBeVisible();
  await userEvent.click(screen.getByText('Ответ брокера'));
  expect(screen.getByText('Полный ответ не сохранён')).toBeVisible();
});

it('shows truncation and copies only the server-projected payload', async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  render(<BrokerExchange exchange={{ request: { content: { messages: ['safe'], token: '[REDACTED]' }, truncated: true, redacted: true } }} />);
  await user.click(screen.getByText('Запрос брокера'));
  expect(screen.getByText('Данные усечены по лимиту хранения.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Копировать запрос' }));
  expect(JSON.parse(copy.mock.calls[0][0])).toEqual({ messages: ['safe'], token: '[REDACTED]' });
});
