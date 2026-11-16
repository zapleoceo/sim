import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi } from '@/test/fake-api';
import { PersonaAgentPrompt } from './persona-agent-prompt';

it('saves the prompt directly without a mode confirmation dialog', async () => {
  let saved = 'Initial prompt';
  const log = fakeApi({
    'GET /api/personas/1/prompt': () => ({ agent_prompt: saved }),
    'PUT /api/personas/1/prompt': req => {
      saved = (req.body as { text: string }).text;
      return { mode: 'unified', agent_prompt: saved };
    },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>
    <PersonaAgentPrompt personaId={1} />
  </QueryClientProvider>);
  const input = await screen.findByRole('textbox', { name: 'Промпт' });
  expect(log.every(r => r.method === 'GET')).toBe(true);
  await userEvent.clear(input);
  await userEvent.type(input, 'New synthetic instructions');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить промпт' }));
  await waitFor(() => expect(log.filter(r => r.method === 'PUT')).toHaveLength(1));
  expect(saved).toBe('New synthetic instructions');
});
