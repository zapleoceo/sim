import { waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp, screen } from '@/test/render-app';

const funnel = {
  stages: [
    {
      stage: 'first_contact',
      count: 1,
      clients: [{ id: 7, display_name: 'Анна', avatar_url: '', stage: 'first_contact', status: 'active' }],
    },
    {
      stage: 'paid',
      count: 1,
      clients: [{ id: 9, display_name: 'Борис', avatar_url: '', stage: 'paid', status: 'paused' }],
    },
  ],
  statuses: { active: 1, paused: 1, no_response: 0, refused: 0 },
};

describe('FunnelPage', () => {
  it('shows columns, status tiles and locks partner-only cards', async () => {
    fakeApi({ 'GET /api/personas': { items: [] }, 'GET /api/funnel': funnel });
    await renderApp('/funnel');

    const first = await screen.findByRole('region', { name: 'Первый контакт' });
    expect(within(first).getByText('Анна')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Статусы клиентов' })).toHaveTextContent('Пауза');
    const paid = screen.getByRole('region', { name: 'Оплата' });
    expect(within(paid).getByText('Борис')).toBeInTheDocument();
    expect(within(paid).queryByRole('button', { name: /Переместить/ })).not.toBeInTheDocument();
  });

  it('moves a card via the menu and rolls back when the server refuses', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [] },
      'GET /api/funnel': funnel,
      'POST /api/clients/7/stage': reply(422, { detail: 'nope' }),
    });
    const user = userEvent.setup();
    await renderApp('/funnel');

    await user.click(await screen.findByRole('button', { name: 'Переместить: Анна' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Вовлечён' }));

    await waitFor(() => expect(log.some((r) => r.method === 'POST' && r.path === '/api/clients/7/stage')).toBe(true));
    const post = log.find((r) => r.method === 'POST')!;
    expect(post.body).toEqual({ stage: 'engaged' });

    const first = screen.getByRole('region', { name: 'Первый контакт' });
    await waitFor(() => expect(within(first).getByText('Анна')).toBeInTheDocument());
    expect(within(screen.getByRole('region', { name: 'Вовлечён' })).queryByText('Анна')).not.toBeInTheDocument();
  });
});
