import userEvent from '@testing-library/user-event';
import { waitFor } from '@testing-library/react';
import type { Client } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { renderApp, screen } from '@/test/render-app';

function makeClient(patch: Partial<Client> = {}): Client {
  return {
    id: 7,
    display_name: 'Анна Клиентова',
    avatar_url: '',
    persona_id: 1,
    stage: 'engaged',
    status: 'active',
    next_step: 'Напомнить про ссылку',
    next_step_at: null,
    notes: null,
    needs: [],
    channels: [{ kind: 'telegram', dialog_id: 1 }],
    last_contact_at: null,
    created_at: '2026-11-01T10:00:00Z',
    ...patch,
  };
}

describe('ClientsPage', () => {
  it('shows clients and sends the search query to the API', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [] },
      'GET /api/clients': { items: [makeClient()], total: 1 },
    });
    await renderApp('/clients');
    expect(await screen.findByText('Анна Клиентова')).toBeInTheDocument();
    expect(screen.getByText('Напомнить про ссылку')).toBeInTheDocument();
    expect(screen.getByText('1 в выборке')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('searchbox', { name: 'Поиск по имени' }), 'анна');
    await waitFor(() =>
      expect(log.some((r) => r.path === '/api/clients' && r.query.get('q') === 'анна' && r.query.get('offset') === '0')).toBe(
        true,
      ),
    );
  });

  it('opens the client card on row click', async () => {
    fakeApi({
      'GET /api/personas': { items: [] },
      'GET /api/clients': { items: [makeClient()], total: 1 },
      'GET /api/clients/7': { ...makeClient(), events: [], partner_events: [] },
      'GET /api/clients/7/messages': { items: [], total: 0, limit: 25, offset: 0 },
      'GET /api/channels': { items: [] },
    });
    const { router } = await renderApp('/clients');
    await userEvent.click(await screen.findByText('Напомнить про ссылку'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/clients/7'));
  });
});
