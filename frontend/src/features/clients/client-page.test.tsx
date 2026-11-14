import userEvent from '@testing-library/user-event';
import { waitFor, within } from '@testing-library/react';
import type { MessageActivity } from '@/lib/activity-models';
import type { ClientCard } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { CSRF, renderApp, screen, testUser } from '@/test/render-app';
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

  it('changes the stage and the status through one dropdown and the stage API', async () => {
    const log = fakeApi({ ...common, 'GET /api/clients/7': card, 'POST /api/clients/7/stage': { ...card, status: 'paused' } });
    await renderApp('/clients/7');
    const posts = () => log.filter((r) => r.method === 'POST' && r.path === '/api/clients/7/stage');

    await userEvent.click(await screen.findByRole('combobox', { name: 'Статус' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Пауза' }));
    await userEvent.type(screen.getByLabelText('Причина (необязательно)'), 'просил написать в пятницу');
    await userEvent.click(screen.getByRole('button', { name: 'Применить' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].body).toEqual({ status: 'paused', reason: 'просил написать в пятницу' });
    expect(posts()[0].headers['X-CSRF-Token']).toBe(CSRF);
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole('combobox', { name: 'Этап' }));
    const stages = await screen.findByRole('listbox', { name: 'Этап' });
    expect(within(stages).getByRole('option', { name: 'Оплата' })).toBeDisabled();
    await userEvent.click(within(stages).getByRole('option', { name: 'Интерес' }));
    await userEvent.click(screen.getByRole('button', { name: 'Применить' }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1].body).toEqual({ stage: 'interested' });
  });

  it('shows the dialog summary read-only with its coverage and extends it on «Обновить»', async () => {
    const summary = { text: 'Обсуждали курс, Анна сомневалась в цене.', covered_until: '2026-11-09T10:00:00Z',
      messages_covered: 14, updated_at: '2026-11-11T10:00:00Z', model: 'gemini/flash', pending: 3 };
    const log = fakeApi({
      ...common,
      'GET /api/clients/7': card,
      'GET /api/dialogs/1/summary': summary,
      'POST /api/dialogs/1/summary': { ...summary, text: 'Обсуждали курс и рассрочку.', messages_covered: 17, pending: 0 },
    });
    await renderApp('/clients/7');
    await userEvent.click(await screen.findByText('Саммари переписки'));
    const block = screen.getByRole('region', { name: 'Саммари диалога' });
    expect(await within(block).findByText('Обсуждали курс, Анна сомневалась в цене.')).toBeInTheDocument();
    expect(block).toHaveTextContent(/Охватывает переписку до .+ · 14 сообщений/);
    expect(block).toHaveTextContent('Ещё не вошло в саммари: 3 сообщения.');
    expect(within(block).queryByRole('textbox')).not.toBeInTheDocument();      // read only

    await userEvent.click(within(block).getByRole('button', { name: 'Обновить' }));
    expect(await within(block).findByText('Обсуждали курс и рассрочку.')).toBeInTheDocument();
    const sent = log.find((r) => r.method === 'POST' && r.path === '/api/dialogs/1/summary');
    expect(sent?.headers['X-CSRF-Token']).toBe(CSRF);
    expect(block).toHaveTextContent(/17 сообщений/);
    expect(block).not.toHaveTextContent('Ещё не вошло');
  });

  it('says that there is no summary yet while the whole dialog fits the context window', async () => {
    fakeApi({ ...common, 'GET /api/clients/7': card, 'GET /api/dialogs/1/summary': {
      text: '', covered_until: null, messages_covered: 0, updated_at: null, model: null, pending: 0 } });
    await renderApp('/clients/7');
    await userEvent.click(await screen.findByText('Саммари переписки'));
    const block = screen.getByRole('region', { name: 'Саммари диалога' });
    expect(await within(block).findByText(/саммари появится, когда часть переписки станет старше окна контекста/)).toBeInTheDocument();
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

describe('ClientPage «Очистить паспорт»', () => {
  const full: ClientCard = {
    ...card, passport: 'Кто он: Анна, двое детей.', passport_updated_at: '2026-11-12T09:00:00Z', next_step: 'дать ссылку',
  };
  const emptied = { ...full, passport: '', passport_updated_at: null, needs: [], notes: '', next_step: '', next_step_at: null };

  it('asks first, then forgets the passport with CSRF and shows the emptied card without a reload', async () => {
    let cleared = false;
    const log = fakeApi({
      ...common,
      'GET /api/clients/7': () => (cleared ? emptied : full),
      'DELETE /api/clients/7/passport': () => {
        cleared = true;
        return emptied;
      },
    });
    await renderApp('/clients/7');
    await userEvent.click(await screen.findByRole('button', { name: /Очистить паспорт/ }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('Восстановить их нельзя');
    expect(dialog).toHaveTextContent('Клиент, его диалоги и сообщения');
    expect(dialog).toHaveTextContent('В Telegram ничего не удаляется');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Очистить паспорт' }));

    await waitFor(() => {
      const sent = log.find((r) => r.method === 'DELETE' && r.path === '/api/clients/7/passport');
      expect(sent?.headers['X-CSRF-Token']).toBe(CSRF);
    });
    expect(await screen.findByText(/паспортистка заполнит его/)).toBeInTheDocument();
    expect(screen.getByLabelText('Заметки')).toHaveValue('');
    expect(screen.queryByText('доход')).not.toBeInTheDocument();
    expect(screen.getByText('Привет')).toBeInTheDocument();   // the chat stays
  });

  it('is not shown to an operator', async () => {
    fakeApi({ ...common, 'GET /api/clients/7': full });
    await renderApp('/clients/7', { user: testUser('operator') });
    await screen.findByText('Привет');
    expect(screen.queryByRole('button', { name: /Очистить паспорт/ })).not.toBeInTheDocument();
  });
});
