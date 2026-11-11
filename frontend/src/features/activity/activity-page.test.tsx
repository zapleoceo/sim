import { waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { MessageActivity } from '@/lib/activity-models';
import type { TraceDetail, TraceStep } from '@/lib/trace-models';
import { fakeApi, type FakeRequest } from '@/test/fake-api';
import { renderApp, screen, testUser } from '@/test/render-app';
import { feedItems } from './activity-feed';

const message: MessageActivity = {
  id: 501,
  direction: 'out',
  author: 'ai',
  text: 'Добрый день!',
  created_at: '2026-11-11T09:00:00Z',
  delivery: 'sent',
  model: 'gpt-x',
  provider: 'broker',
  dialog_id: 1,
  client: { id: 7, display_name: 'Анна Клиентова', avatar_url: '' },
  persona: { id: 1, name: 'Марина' },
  channel: { id: 3, name: 'Бот', kind: 'telegram', account_label: '@sim_bot' },
  sent_at: '2026-11-11T09:00:05Z',
};

const TRACE = 'a'.repeat(32);

const step: TraceStep = {
  id: 9, trace_id: TRACE, kind: 'job', name: 'reply', status: 'retry', started_at: '2026-11-11T09:00:00Z',
  duration_ms: 1200, attempt: 2, http_status: null, actor: 'worker', error: 'LLMError: timeout', detail: {},
  client: { id: 7, display_name: 'Анна Клиентова' }, admin_id: null, dialog_id: 1, channel_id: 3, job_id: 4,
  message_id: null, llm_call_id: null,
};

const trace: TraceDetail = {
  trace_id: TRACE, started_at: '2026-11-11T09:00:00Z', finished_at: '2026-11-11T09:00:02Z', duration_ms: 2000,
  status: 'ok', cost_usd: 0.002,
  steps: [
    { ...step, id: 1, kind: 'webhook', name: 'POST /hooks/telegram/3', status: 'ok', attempt: null, error: null,
      http_status: 200, offset_ms: 0, in_trace: true },
    { ...step, id: 2, kind: 'llm', name: 'reply · chat:sales', status: 'ok', attempt: null, error: null, offset_ms: 300,
      in_trace: true, llm: { model: 'gpt-x', provider: 'broker', tokens_in: 100, tokens_out: 20, cached_tokens: 0,
        cost_usd: 0.002, finish_reason: 'stop' } },
    { ...step, id: 3, offset_ms: 800, in_trace: true, detail: { queue_ms: 1500, outcome: 'sent' } },
  ],
};

const routes = {
  'GET /api/channels': { items: [] },
  'GET /api/personas': { items: [] },
  'GET /api/activity/messages': { items: [message], total: 1, limit: 25, offset: 0 },
  'GET /api/activity/llm': { items: [], total: 0, limit: 25, offset: 0 },
  'GET /api/activity/log': { items: [step], total: 1, page: 1, size: 50, retention_days: 30 },
  [`GET /api/activity/traces/${TRACE}`]: trace,
};

const lastLogQuery = (log: FakeRequest[]): URLSearchParams | undefined =>
  log.filter((r) => r.path === '/api/activity/log').at(-1)?.query;

describe('ActivityPage · system log', () => {
  it('is the owner default, filtered by the channel from the URL', async () => {
    const log = fakeApi(routes);
    await renderApp('/activity?channel_id=3');
    expect(await screen.findByRole('tab', { name: 'Журнал', selected: true })).toBeInTheDocument();
    expect(await screen.findByText('LLMError: timeout')).toBeInTheDocument();
    expect(screen.getByText('Повтор')).toBeInTheDocument();
    expect(screen.getByText('1,2 с')).toBeInTheDocument();
    await waitFor(() => expect(lastLogQuery(log)?.get('channel_id')).toBe('3'));
    expect(lastLogQuery(log)?.get('sort')).toBe('started_at');
  });

  it('sorts and filters from the column headers on the server', async () => {
    const log = fakeApi(routes);
    const user = userEvent.setup();
    await renderApp('/activity');
    await screen.findByText('LLMError: timeout');
    await user.click(screen.getByRole('button', { name: 'Сортировать: Длительность' }));
    await waitFor(() => expect(lastLogQuery(log)?.get('sort')).toBe('duration_ms'));
    expect(lastLogQuery(log)?.get('order')).toBe('desc');
    await user.click(screen.getByRole('button', { name: 'Сортировать: Длительность' }));
    await waitFor(() => expect(lastLogQuery(log)?.get('order')).toBe('asc'));

    await user.click(screen.getByRole('button', { name: 'Фильтр: Тип' }));
    await user.click(await screen.findByRole('checkbox', { name: 'Модель' }));
    await waitFor(() => expect(lastLogQuery(log)?.get('kind')).toBe('llm'));
    await user.keyboard('{Escape}');

    await user.click(screen.getByRole('button', { name: 'Только ошибки' }));
    await waitFor(() => expect(lastLogQuery(log)?.get('has_error')).toBe('true'));
    expect(lastLogQuery(log)?.get('page')).toBe('1');
    expect(screen.getByRole('button', { name: /Сбросить фильтры \(2\)/ })).toBeInTheDocument();
  });

  it('opens the whole trace in a side panel', async () => {
    fakeApi(routes);
    const user = userEvent.setup();
    await renderApp('/activity');
    await user.click(await screen.findByRole('row', { name: /Открыть трейс aaaaaaaa/ }));
    const steps = await screen.findByRole('list', { name: 'Шаги запроса' });
    expect(within(steps).getByText('POST /hooks/telegram/3')).toBeInTheDocument();
    expect(within(steps).getByText('gpt-x')).toBeInTheDocument();
    expect(within(steps).getByText('Ожидание в очереди')).toBeInTheDocument();
    expect(within(steps).getByText('Попытка 2')).toBeInTheDocument();
  });

  it('never shows or loads the log for operators', async () => {
    const log = fakeApi(routes);
    await renderApp(`/activity?trace=${TRACE}`, { user: testUser('operator') });
    expect(await screen.findByText('Добрый день!')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Журнал' })).not.toBeInTheDocument();
    expect(log.some((r) => r.path.startsWith('/api/activity/log') || r.path.startsWith('/api/activity/traces'))).toBe(false);
  });
});

describe('ActivityPage', () => {
  it('shows messages and pre-fills the channel from the URL', async () => {
    const log = fakeApi(routes);
    await renderApp('/activity?tab=messages&channel_id=3');
    expect(await screen.findByText('Добрый день!')).toBeInTheDocument();
    expect(screen.getByText('Персонаж: Марина')).toBeInTheDocument();
    await waitFor(() =>
      expect(log.some((r) => r.path === '/api/activity/messages' && r.query.get('channel_id') === '3')).toBe(true),
    );
  });

  it('hides the LLM log from operators even with ?tab=llm', async () => {
    const log = fakeApi(routes);
    await renderApp('/activity?tab=llm', { user: testUser('operator') });
    expect(await screen.findByText('Добрый день!')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Вызовы ИИ' })).not.toBeInTheDocument();
    expect(log.some((r) => r.path === '/api/activity/llm')).toBe(false);
  });

  it('owner sees the LLM tab', async () => {
    fakeApi(routes);
    await renderApp('/activity?tab=llm');
    expect(await screen.findByRole('tab', { name: 'Вызовы ИИ', selected: true })).toBeInTheDocument();
    expect(await screen.findByText('Вызовов не найдено')).toBeInTheDocument();
  });
});

describe('feedItems', () => {
  it('reverses a client page and adds day separators', () => {
    const a = { ...message, id: 1, created_at: '2026-11-10T09:00:00Z' };
    const b = { ...message, id: 2, created_at: '2026-11-11T09:00:00Z' };
    const items = feedItems([b, a], true);
    expect(items.map((i) => i.message.id)).toEqual([1, 2]);
    expect(items.every((i) => i.day !== '')).toBe(true);
    expect(feedItems([b, a], false).map((i) => i.message.id)).toEqual([2, 1]);
  });
});
