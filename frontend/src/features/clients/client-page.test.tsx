import userEvent from '@testing-library/user-event';
import { waitFor } from '@testing-library/react';
import type { MessageActivity } from '@/lib/activity-models';
import type { ClientCard } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp, screen } from '@/test/render-app';
import { CHAT_PAGE } from './client-chat';

const card: ClientCard = {
  id: 7,
  display_name: 'Анна Клиентова',
  avatar_url: '',
  persona_id: 1,
  stage: 'engaged',
  status: 'active',
  next_step: '',
  next_step_at: null,
  notes: 'Любит короткие ответы',
  needs: ['доход'],
  channels: [{ kind: 'telegram', dialog_id: 1 }],
  last_contact_at: null,
  created_at: '2026-11-01T10:00:00Z',
  events: [{ type: 'stage', from_stage: 'first_contact', to_stage: 'engaged', actor: 'ai', created_at: '2026-11-01T10:05:00Z' }],
  partner_events: [],
};

function msg(id: number, author: 'client' | 'ai', text: string): MessageActivity {
  return {
    id, dialog_id: 1, direction: author === 'client' ? 'in' : 'out', author, text, created_at: `2026-11-01T10:0${id % 10}:00Z`,
    delivery: author === 'ai' ? 'sent' : 'received', model: author === 'ai' ? 'gemini/flash' : null, provider: null,
    tokens_in: 2900, tokens_out: 80, cost_usd: 0, media_kind: null, media_url: null, guard: null,
    client: { id: 7, display_name: 'Анна Клиентова' }, persona: { id: 1, name: 'Марина' },
    channel: { id: 1, kind: 'telegram', name: 'Marina' }, sent_at: null,
  } as unknown as MessageActivity;
}

const chat = { items: [msg(2, 'ai', 'Здравствуйте, я Марина'), msg(1, 'client', 'Привет')], total: 2, limit: CHAT_PAGE, offset: 0 };
const common = { 'GET /api/clients/7/messages': chat };

describe('ClientPage', () => {
  it('shows «not found» for a missing client', async () => {
    fakeApi({ 'GET /api/clients/99': reply(404, { detail: 'not found' }) });
    await renderApp('/clients/99');
    expect(await screen.findByText('Клиент не найден')).toBeInTheDocument();
  });

  it('is calm: the chat as bubbles without technical details, the rest in one side panel', async () => {
    fakeApi({ ...common, 'GET /api/clients/7': card });
    await renderApp('/clients/7');
    expect(await screen.findByText('Здравствуйте, я Марина')).toBeInTheDocument();
    expect(screen.getByText('Привет')).toBeInTheDocument();
    expect(screen.queryByText(/Генератор/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Токены/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Этап' })).toHaveTextContent('Вовлечён');
    expect(screen.getByRole('combobox', { name: 'Статус' })).toHaveTextContent('Активен');
    expect(screen.getByText('доход')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Открыть диалог/ })).toHaveAttribute('href', '/inbox?d=1');
  });

  it('scrolls the chat inside its own box, so the page keeps the header in view', async () => {
    fakeApi({ ...common, 'GET /api/clients/7': card });
    await renderApp('/clients/7');
    const bubble = await screen.findByText('Привет');
    const box = bubble.closest('.overflow-y-auto');
    expect(box).not.toBeNull();
    expect(screen.getByRole('heading', { name: /Анна Клиентова/ })).toBeInTheDocument();
  });

  it('saves notes and the next step by themselves when the field loses focus', async () => {
    const log = fakeApi({
      ...common,
      'GET /api/clients/7': card,
      'PATCH /api/clients/7': (req) => ({ ...card, ...(req.body as object) }),
    });
    await renderApp('/clients/7');
    const notes = await screen.findByLabelText('Заметки');
    await userEvent.click(notes);
    await userEvent.tab();
    expect(log.some((r) => r.method === 'PATCH')).toBe(false); // nothing changed, nothing sent

    await userEvent.clear(notes);
    await userEvent.type(notes, 'Перезвонить');
    await userEvent.tab();
    await waitFor(() => expect(log.find((r) => r.method === 'PATCH')?.body).toEqual({ notes: 'Перезвонить' }));

    const step = screen.getByLabelText('Следующий шаг');
    await userEvent.type(step, 'дать ссылку{Enter}');
    await waitFor(() =>
      expect(log.filter((r) => r.method === 'PATCH').at(-1)?.body).toEqual({ next_step: 'дать ссылку' }),
    );
  });

  it('keeps the passport and the history folded', async () => {
    fakeApi({
      ...common,
      'GET /api/clients/7': { ...card, passport: 'Кто он: Анна, двое детей.', passport_updated_at: '2026-11-12T09:00:00Z' },
    });
    await renderApp('/clients/7');
    const passport = (await screen.findByText('Паспорт собеседника')).closest('details')!;
    expect(passport).not.toHaveAttribute('open');
    await userEvent.click(screen.getByText('Паспорт собеседника'));
    expect(passport).toHaveAttribute('open');
    expect(screen.getByText('Кто он: Анна, двое детей.')).toBeInTheDocument();
    expect(screen.getByText('История · 1')).toBeInTheDocument();
  });

  it('shows an empty passport hint for a new lead', async () => {
    fakeApi({ ...common, 'GET /api/clients/7': card });
    await renderApp('/clients/7');
    expect(await screen.findByText(/паспортистка заполнит его/)).toBeInTheDocument();
  });

  it('loads older messages on «Показать раньше»', async () => {
    const log = fakeApi({
      'GET /api/clients/7': card,
      'GET /api/clients/7/messages': (req) => ({ ...chat, total: 40, limit: Number(req.query.get('limit') ?? CHAT_PAGE) }),
    });
    await renderApp('/clients/7');
    await userEvent.click(await screen.findByRole('button', { name: 'Показать раньше' }));
    await waitFor(() =>
      expect(log.filter((r) => r.path === '/api/clients/7/messages').at(-1)?.query.get('limit')).toBe(String(CHAT_PAGE * 2)),
    );
  });
});
