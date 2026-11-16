import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi } from '@/test/fake-api';
import { PersonaAgentPrompt } from './persona-agent-prompt';

const version = (id: number, text: string, note = '') => ({
  id, text, author: 'Дима', note, created_at: `2026-11-1${id}T10:00:00Z`,
});

function renderPrompt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>
    <PersonaAgentPrompt personaId={1} />
  </QueryClientProvider>);
}

it('saves the prompt directly without a mode confirmation dialog', async () => {
  let saved = 'Initial prompt';
  const log = fakeApi({
    'GET /api/personas/1/prompt': () => ({ agent_prompt: saved }),
    'GET /api/personas/1/prompt/versions': () => ({ items: [] }),
    'PUT /api/personas/1/prompt': req => {
      saved = (req.body as { text: string }).text;
      return { mode: 'unified', agent_prompt: saved };
    },
  });
  renderPrompt();
  const input = await screen.findByRole('textbox', { name: 'Промпт' });
  expect(log.every(r => r.method === 'GET')).toBe(true);
  await userEvent.clear(input);
  await userEvent.type(input, 'New synthetic instructions');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить промпт' }));
  await waitFor(() => expect(log.filter(r => r.method === 'PUT')).toHaveLength(1));
  expect(saved).toBe('New synthetic instructions');
});

it('lists the prompt versions newest first and restores an older one', async () => {
  let current = 'Вторая версия';
  let items = [version(2, 'Вторая версия'), version(1, 'Первая версия', 'версия на момент включения истории')];
  const log = fakeApi({
    'GET /api/personas/1/prompt': () => ({ agent_prompt: current }),
    'GET /api/personas/1/prompt/versions': () => ({ items }),
    'POST /api/personas/1/prompt/versions/1/restore': () => {
      current = 'Первая версия';
      items = [version(3, current, 'восстановлена версия от 11.11.2026 10:00 UTC'), ...items];
      return { mode: 'unified', agent_prompt: current, items };
    },
  });
  renderPrompt();
  await userEvent.click(await screen.findByText('Версии промпта (2)'));
  const list = screen.getByRole('list', { name: 'Версии промпта' });
  const rows = within(list).getAllByRole('listitem');
  expect(rows[0]).toHaveTextContent('текущая');
  expect(within(rows[0]).queryByRole('button', { name: 'Восстановить' })).not.toBeInTheDocument();
  expect(rows[1]).toHaveTextContent('версия на момент включения истории');
  await userEvent.click(within(rows[1]).getByRole('button', { name: 'Восстановить' }));
  await waitFor(() => expect(log.some(r => r.method === 'POST')).toBe(true));
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Промпт' })).toHaveValue('Первая версия'));
  expect(await screen.findByText('Версии промпта (3)')).toBeInTheDocument();
});
