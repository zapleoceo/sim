import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { SimBranchRow } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { renderApp } from '@/test/render-app';
import { adminLabel, mergePages } from './simulations-page';

const ROW: SimBranchRow = {
  id: 4,
  admin: { id: 1, display_name: 'Дима' },
  persona: { id: 2, name: 'Марина Светлова' },
  title: 'Марина · 10.11 12:00',
  message_count: 2,
  stage: 'engaged',
  status: 'active',
  created_at: '2026-11-10T12:00:00Z',
  last_active_at: '2026-11-10T12:05:00Z',
  closed_at: null,
};

describe('simulations', () => {
  it('labels numeric admin names as IDs', () => {
    expect(adminLabel('169510539')).toBe('ID 169510539');
    expect(adminLabel('Дима')).toBe('Дима');
  });

  it('merges pages without duplicates', () => {
    expect(mergePages([{ items: [ROW] }, { items: [ROW, { ...ROW, id: 5 }] }]).map((r) => r.id)).toEqual([4, 5]);
  });

  it('lists branches and shows a read-only transcript with the guard badge and model', async () => {
    fakeApi({
      'GET /api/simulations': { items: [ROW], total: 1 },
      'GET /api/simulations/4': {
        ...ROW,
        needs: ['сменить профессию'],
        next_step: 'уточнить опыт',
        notes: '',
        messages: [
          { id: 1, role: 'client', text: 'Здравствуйте', model: null, created_at: '2026-11-10T12:00:00Z', guard: null },
          {
            id: 2, role: 'ai', text: 'Привет!', model: 'fake-model', created_at: '2026-11-10T12:00:05Z',
            guard: { verdict: 'rewritten', violations: ['тон'], model: null, original_text: 'Привет' },
          },
        ],
      },
    });
    await renderApp('/simulations');
    const item = await screen.findByRole('button', { name: /Марина Светлова/ });
    expect(item).toHaveTextContent('Дима');
    expect(screen.queryByText(/Показать ещё/)).not.toBeInTheDocument();

    await userEvent.click(item);
    expect(await screen.findByText('Привет!')).toBeInTheDocument();
    expect(screen.getByText(/fake-model/)).toBeInTheDocument();
    expect(screen.getByText('Исправлено контролером')).toBeInTheDocument();
    expect(screen.getByText(/Потребности: сменить профессию/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByText('Привет!')).not.toBeInTheDocument();
  });

  it('loads the next page by offset', async () => {
    const log = fakeApi({
      'GET /api/simulations': (r: { query: URLSearchParams }) =>
        r.query.get('offset') === '0' ? { items: [ROW], total: 2 } : { items: [{ ...ROW, id: 9, persona: { id: 3, name: 'Анна' } }], total: 2 },
    });
    await renderApp('/simulations');
    await userEvent.click(await screen.findByRole('button', { name: 'Показать ещё (1)' }));
    expect(await screen.findByRole('button', { name: /Анна/ })).toBeInTheDocument();
    expect(log.filter((r) => r.path === '/api/simulations').map((r) => r.query.get('offset'))).toEqual(['0', '1']);
  });

  it('shows the empty state', async () => {
    fakeApi({ 'GET /api/simulations': { items: [], total: 0 } });
    await renderApp('/simulations');
    expect(await screen.findByText('Симуляций пока нет')).toBeInTheDocument();
  });
});
