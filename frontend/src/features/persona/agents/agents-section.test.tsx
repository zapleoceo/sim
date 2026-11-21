import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { PersonaAgents, PersonaAgentsPatch } from '@/lib/types';
import { fakeApi, type FakeRequest } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { PersonaIdProvider } from '../persona-scope';
import { MODELS, REGISTRY, SAVED } from './agents-fixtures';
import { AgentsSection } from './agents-section';

function backend(extra: Record<string, (req: FakeRequest) => unknown> = {}): FakeRequest[] {
  return fakeApi({
    'GET /api/agents/registry': REGISTRY,
    'GET /api/personas/1/agents': SAVED,
    'GET /api/models': { items: MODELS },
    ...extra,
  });
}

/** PUT /personas/1/agents as the backend answers it: the sent agents, rules and «Внимание». */
function saveHandler(req: FakeRequest): PersonaAgents {
  const body = req.body as PersonaAgentsPatch;
  const agents = { ...SAVED.agents };
  for (const [key, value] of Object.entries(body.agents)) agents[key] = { ...value, effective_model: value.model ?? 'gpt-text' };
  return { agents, guard_rules: body.guard_rules, attention: body.attention };
}

const render = () => renderWithApp(<PersonaIdProvider id={1}><AgentsSection /></PersonaIdProvider>);
const card = (title: string) => screen.getByRole('article', { name: title });

describe('AgentsSection', () => {
  it('shows the agents by group with filtered models, availability and the default tier', async () => {
    backend();
    await render();
    expect(await screen.findByRole('heading', { name: 'Ответ' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Фон' })).toBeInTheDocument();

    const reply = within(card('Собеседник'));
    const replyModel = reply.getByLabelText<HTMLSelectElement>('Модель');
    expect(replyModel.value).toBe('gpt-text');
    expect(reply.getByRole('option', { name: 'cheap-text — недоступна' })).toBeInTheDocument();
    expect(reply.queryByRole('option', { name: 'gemini-tts' })).not.toBeInTheDocument();   // filtered out
    expect(reply.getByText('gpt-text — доступна')).toBeInTheDocument();
    expect(reply.getByRole('button', { name: /Платный/, pressed: true })).toHaveTextContent('станд.');
    expect(reply.getByText('# Как отвечать')).toBeInTheDocument();                         // read-only rules

    // A saved model that left the catalog is kept.
    const tts = within(card('Голос'));
    expect(tts.getByLabelText<HTMLSelectElement>('Модель').value).toBe('retired-tts');
    expect(tts.getByRole('option', { name: 'retired-tts — нет в каталоге Broker' })).toBeInTheDocument();
    expect(tts.queryByLabelText('max_tokens')).not.toBeInTheDocument();

    // «By default» explains itself; the planner may be switched off.
    const guard = within(card('Охранник'));
    expect(guard.getByRole('option', { name: 'Как у агента «Собеседник»' })).toBeInTheDocument();
    expect(guard.queryByRole('option', { name: 'Не использовать' })).not.toBeInTheDocument();
    const life = within(card('Планировщик'));
    expect(life.getByRole('option', { name: 'По умолчанию: codex-sub/gpt-6.1-sol' })).toBeInTheDocument();
    expect(life.getByRole('option', { name: 'Не использовать' })).toBeInTheDocument();
    expect(life.getByText(/подписка: ключи Broker не тратятся/)).toBeInTheDocument();
  });

  it('saves the changed agents and the guard rules with one Save', async () => {
    const log = backend({
      'PUT /api/personas/1/agents': saveHandler,
    });
    await render();
    const guard = within(await screen.findByRole('article', { name: 'Охранник' }));
    expect(screen.getByText('Агенты сохранены')).toBeInTheDocument();

    await userEvent.click(guard.getByRole('button', { name: /Бесплатный/ }));
    await userEvent.selectOptions(guard.getByLabelText('Модель'), 'cheap-text');
    const tokens = guard.getByLabelText('max_tokens');
    await userEvent.clear(tokens);
    await userEvent.type(tokens, '300');
    const rules = guard.getByLabelText('Запреты');
    await userEvent.clear(rules);
    await userEvent.type(rules, 'Без фамилий');
    expect(screen.getByText('Агенты изменены, не сохранены')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Сохранить агентов' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PUT')?.body).toEqual({
      agents: { guard: { model: 'cheap-text', key_tier: 'free', prompt: '', max_tokens: 300 } },
      guard_rules: 'Без фамилий',
      attention: SAVED.attention,
    });
    expect(await screen.findByText('Агенты сохранены')).toBeInTheDocument();
  });

  it('tunes «Внимание» on its card and saves it with the agents', async () => {
    const log = backend({ 'PUT /api/personas/1/agents': saveHandler });
    await render();
    const attention = within(await screen.findByRole('article', { name: 'Внимание' }));
    expect(attention.getByLabelText<HTMLSelectElement>('Модель').value).toBe('');      // by default
    expect(attention.getByRole('option', { name: 'По умолчанию: gemini/gemini-3.5-flash-lite' })).toBeInTheDocument();
    expect(attention.getByRole('button', { name: 'Как живой человек', pressed: true })).toBeInTheDocument();

    await userEvent.click(attention.getByRole('button', { name: 'Только когда свободна' }));
    fireEvent.change(attention.getByLabelText(/Темп/), { target: { value: '1.5' } });
    await userEvent.click(attention.getByRole('switch', { name: 'Может оставить прочитанным' }));
    expect(screen.getByText('Агенты изменены, не сохранены')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Сохранить агентов' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PUT')?.body).toEqual({
      agents: {},
      guard_rules: SAVED.guard_rules,
      attention: { mode: 'free_only', pace: 1.5, may_leave_read: false, after_missed_call: true, night_dnd: true },
    });
    expect(await screen.findByText('Агенты сохранены')).toBeInTheDocument();
  });

  it('edits a prompt, resets it to the default and blocks a bad max_tokens', async () => {
    const log = backend();
    await render();
    const guard = within(await screen.findByRole('article', { name: 'Охранник' }));
    expect(guard.getByText('стандартный')).toBeInTheDocument();
    expect(guard.getByText('Код подставит: {name}, {language}.')).toBeInTheDocument();
    const prompt = guard.getByLabelText<HTMLTextAreaElement>('Охранник: промт');
    expect(prompt.value).toBe('Проверь ответ {name}.');
    await userEvent.type(prompt, ' Без мата.');
    expect(prompt.value).toBe('Проверь ответ {name}. Без мата.');
    expect(guard.getByText('свой')).toBeInTheDocument();
    expect(screen.getByText('Агенты изменены, не сохранены')).toBeInTheDocument();
    // The editor sits in a collapsible block: `hidden` keeps the query independent of it.
    await userEvent.click(guard.getByRole('button', { name: 'Сбросить к стандартному', hidden: true }));
    expect(prompt.value).toBe('Проверь ответ {name}.');
    expect(screen.getByText('Агенты сохранены')).toBeInTheDocument();          // back to the saved state

    const tokens = guard.getByLabelText('max_tokens');
    await userEvent.clear(tokens);
    await userEvent.type(tokens, '10');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить агентов' }));
    expect(await screen.findByText(/Охранник: max_tokens — целое число от 64 до 16384/)).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'PUT')).toHaveLength(0);
  });
});
