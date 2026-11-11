import { waitFor } from '@testing-library/react';
import type { MessageActivity } from '@/lib/activity-models';
import { fakeApi } from '@/test/fake-api';
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

const routes = {
  'GET /api/channels': { items: [] },
  'GET /api/personas': { items: [] },
  'GET /api/activity/messages': { items: [message], total: 1, limit: 25, offset: 0 },
  'GET /api/activity/llm': { items: [], total: 0, limit: 25, offset: 0 },
};

describe('ActivityPage', () => {
  it('shows messages and pre-fills the channel from the URL', async () => {
    const log = fakeApi(routes);
    await renderApp('/activity?channel_id=3');
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
