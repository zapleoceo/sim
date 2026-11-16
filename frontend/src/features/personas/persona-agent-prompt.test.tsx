import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmProvider } from '@/components/confirm';
import { fakeApi } from '@/test/fake-api';
import { PersonaAgentPrompt } from './persona-agent-prompt';

it('does not switch legacy mode on read or cancelled confirmation; saves only explicit owner text', async () => {
  let mode = 'legacy';
  let saved = 'Legacy instructions';
  const log = fakeApi({
    'GET /api/personas/1/prompt': () => ({ mode, agent_prompt: saved }),
    'PUT /api/personas/1/prompt': req => {
      saved = (req.body as { text: string }).text;
      mode = 'unified';
      return { mode, agent_prompt: saved };
    },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ConfirmProvider>
    <PersonaAgentPrompt personaId={1} />
  </ConfirmProvider></QueryClientProvider>);
  const input = await screen.findByRole('textbox', { name: 'Текст единого промпта' });
  expect(log.every(r => r.method === 'GET')).toBe(true);
  await userEvent.clear(input);
  await userEvent.type(input, 'New synthetic instructions');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить единый промпт' }));
  await userEvent.click(screen.getByRole('button', { name: 'Отмена' }));
  expect(log.filter(r => r.method === 'PUT')).toHaveLength(0);
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить единый промпт' }));
  await userEvent.click(screen.getByRole('button', { name: 'Включить', exact: true }));
  await waitFor(() => expect(log.filter(r => r.method === 'PUT')).toHaveLength(1));
  expect(saved).toBe('New synthetic instructions');
  expect(await screen.findByText(/Сейчас работает единый промпт:/)).toBeInTheDocument();
});
