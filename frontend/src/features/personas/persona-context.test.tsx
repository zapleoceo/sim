import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi, reply } from '@/test/fake-api';
import { PersonaContext } from './persona-context';

it('shows no client data by default and drops previous context when a different dialog is selected', async () => {
  const log = fakeApi({ 'GET /api/personas/1/context': req => {
    const id = req.query.get('dialog_id');
    if (id === '2') return reply(404, {});
    return { persona_id: 1, notice: 'Сборка на сейчас', layers: [],
      example_context: { content: 'Пример без клиента', truncated: false },
      snapshot: id ? { content: 'Контекст первого диалога', truncated: false } : null };
  } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PersonaContext personaId={1} /></QueryClientProvider>);
  await screen.findByText('Сборка на сейчас');
  expect(screen.queryByText(/Контекст первого/)).not.toBeInTheDocument();
  const input = screen.getByRole('spinbutton', { name: 'Номер диалога' });
  await userEvent.type(input, '1');
  await userEvent.click(screen.getByRole('button', { name: 'Показать контекст' }));
  await screen.findByText('Сборка для диалога № 1');
  expect(screen.getByText(/Контекст первого/)).toBeInTheDocument();
  await userEvent.clear(input);
  await userEvent.type(input, '2');
  await userEvent.click(screen.getByRole('button', { name: 'Показать контекст' }));
  await waitFor(() => expect(screen.queryByText(/Контекст первого/)).not.toBeInTheDocument());
  expect(await screen.findByText('Контекст недоступен. Проверьте номер диалога и доступ.')).toBeInTheDocument();
  expect(log.every(r => r.method === 'GET')).toBe(true);
});
