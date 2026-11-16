import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DEFAULT_OPEN, SECTIONS } from './persona-sections';
import { EMPTY_PERSONA } from './persona-schema';
import type { Persona } from '@/lib/models';
import { fakeApi, type FakeReply } from '@/test/fake-api';
import { blockButton, openLongText, toggleBlock } from '@/test/persona-blocks';
import { renderApp } from '@/test/render-app';
import { PHONE_WIDTH, stubViewportWidth } from '@/test/viewport';

const AGENT_PROMPT = 'Ты Марина Светлова, психолог. Пиши коротко и по делу.';

const MARINA: Persona = {
  ...EMPTY_PERSONA,
  id: 1,
  name: 'Марина',
  agent_prompt: AGENT_PROMPT,
  routine: 'пн-пт 09:00-13:00 консультации',
  created_at: '2026-11-01T10:00:00Z',
};

function backend(overrides: Record<string, FakeReply> = {}) {
  return fakeApi({
    'GET /api/personas': { items: [MARINA] },
    'GET /api/strategies': { items: [] },
    'GET /api/models': { items: [] },
    'GET /api/personas/1/media': { items: [] },
    'PUT /api/personas/1': (r: { body: unknown }) => ({ ...MARINA, ...(r.body as object) }),
    ...overrides,
  });
}

const savedBody = (log: ReturnType<typeof backend>): Record<string, unknown> | undefined =>
  log.find((r) => r.method === 'PUT' && r.path === '/api/personas/1')?.body as Record<string, unknown> | undefined;

/** The note of the save bar about unsaved edits (toasts are status regions too, so it is taken by its place). */
const unsaved = (): HTMLElement => document.querySelector('[data-slot="save-bar"] [role="status"]') as HTMLElement;

const promptBox = (): HTMLElement =>
  within(screen.getByRole('region', { name: 'Промпт' })).getByRole('textbox', { name: 'Промпт' });

async function save(): Promise<void> {
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить персонажа' }));
}

describe('persona page layout', () => {
  it('has the blocks in order, without shared instructions or an assembled prompt', () => {
    expect(SECTIONS.map((s) => s.label)).toEqual([
      'Профиль', 'Промпт', 'Распорядок', 'Голос и звонки', 'Модель и стратегия', 'Фото и кружочки',
    ]);
    expect(SECTIONS.map((s) => s.id)).not.toContain('shared');
    expect(SECTIONS.map((s) => s.id)).not.toContain('assembled');
    expect([...DEFAULT_OPEN]).toEqual(['profile', 'prompt']);
  });

  it('groups the page into blocks that fold: «Профиль» and «Промпт» are open at first', async () => {
    backend();
    await renderApp('/personas/1');
    expect(await blockButton('Профиль')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Промпт' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Имя')).toBeVisible();
    expect(promptBox()).toBeVisible();
    for (const section of SECTIONS.filter((s) => !DEFAULT_OPEN.has(s.id))) {
      expect(screen.getByRole('button', { name: section.label })).toHaveAttribute('aria-expanded', 'false');
      expect(document.getElementById(`${section.id}-panel`)).not.toBeVisible();
    }
    const order = [...document.querySelectorAll('[data-section]')].map((el) => el.getAttribute('data-section'));
    expect(order).toEqual(['profile', 'prompt', 'routine', 'voice', 'strategy', 'media']);
    expect(screen.queryByText('Общие инструкции')).not.toBeInTheDocument();
    expect(screen.queryByText('Контекст модели')).not.toBeInTheDocument();

    await toggleBlock('Голос и звонки');
    expect(screen.getByRole('button', { name: 'Голос и звонки' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Принимать звонки')).toBeVisible();
    expect(screen.getByLabelText('Имя')).toBeVisible();

    await toggleBlock('Голос и звонки');
    expect(screen.getByLabelText('Принимать звонки')).not.toBeVisible();
    await toggleBlock('Профиль');
    expect(screen.getByLabelText('Имя')).not.toBeVisible();
  });

  it('edits the prompt in the persona form and saves it with the rest: one button, no separate save', async () => {
    const log = backend();
    await renderApp('/personas/1');
    await blockButton('Промпт');
    expect(promptBox()).toHaveValue(AGENT_PROMPT);
    expect(screen.queryByRole('button', { name: 'Сохранить промпт' })).not.toBeInTheDocument();
    expect(screen.getByText(/Не пишите сюда дату рождения, возраст, часовой пояс и распорядок/)).toBeInTheDocument();

    await userEvent.type(promptBox(), ' Без смайликов.');
    expect(unsaved()).toHaveTextContent('Есть несохранённые правки');
    await save();
    await waitFor(() => expect(savedBody(log)).toBeDefined());
    expect(savedBody(log)).toMatchObject({ agent_prompt: `${AGENT_PROMPT} Без смайликов.`, routine: MARINA.routine });
    expect(savedBody(log)).not.toHaveProperty('description');
    expect(savedBody(log)).not.toHaveProperty('prohibitions');
    expect(savedBody(log)).not.toHaveProperty('age');
    expect(log.some((r) => r.path.endsWith('/prompt'))).toBe(false);
  });

  it('warns about field facts copied into the prompt, without blocking the save', async () => {
    const log = backend();
    await renderApp('/personas/1');
    await blockButton('Промпт');
    expect(screen.queryByText(/В промпте найдено то, что уже есть в полях/)).not.toBeInTheDocument();

    await userEvent.type(promptBox(), ' Тебе 45 лет, родилась 1980-01-02.');
    expect(await screen.findByText(
      'В промпте найдено то, что уже есть в полях: дата рождения, возраст. Оставьте это в одном месте — в полях.',
    )).toBeVisible();
    await save();
    await waitFor(() => expect(savedBody(log)).toBeDefined());
  });

  it('does not save a blank prompt and points at it', async () => {
    const log = backend({ 'GET /api/personas': { items: [{ ...MARINA, agent_prompt: '' }] } });
    await renderApp('/personas/1');
    await toggleBlock('Промпт');
    expect(screen.getByRole('button', { name: 'Промпт' })).toHaveAttribute('aria-expanded', 'false');
    await save();
    expect(await screen.findByText('Напишите промпт')).toBeVisible();
    // the header now also says «Проверьте поля»
    expect(screen.getByRole('button', { name: /^Промпт/ })).toHaveAttribute('aria-expanded', 'true');
    expect(log.some((r) => r.method === 'PUT')).toBe(false);
  });

  it('keeps the birth date in one place: a field of «Профиль»', async () => {
    backend();
    await renderApp('/personas/1');
    await blockButton('Профиль');
    expect(screen.getAllByLabelText('Дата рождения')).toHaveLength(1);
    expect(within(screen.getByRole('region', { name: 'Профиль' })).getByLabelText('Дата рождения')).toBeVisible();
    expect(screen.queryByLabelText(/Возраст/)).not.toBeInTheDocument();
  });

  it('shows the routine long text in its full editor', async () => {
    backend();
    await renderApp('/personas/1');
    await toggleBlock('Распорядок');
    expect(await openLongText('Распорядок недели')).toHaveValue('пн-пт 09:00-13:00 консультации');
  });

  it('saves the persona form from a folded block', async () => {
    const log = backend();
    await renderApp('/personas/1');
    const name = await screen.findByLabelText('Имя');
    await userEvent.clear(name);
    await userEvent.type(name, 'Марина Светлова');
    expect(unsaved()).toHaveTextContent('Есть несохранённые правки');
    await toggleBlock('Профиль');
    await save();

    await waitFor(() => expect(savedBody(log)).toBeDefined());
    expect(savedBody(log)).toMatchObject({
      name: 'Марина Светлова', routine: 'пн-пт 09:00-13:00 консультации', agent_prompt: AGENT_PROMPT,
    });
    await waitFor(() => expect(unsaved()).toBeEmptyDOMElement());
  });

  it('shows language in the voice section, not in the profile', async () => {
    backend();
    await renderApp('/personas/1');
    await blockButton('Профиль');
    expect(within(screen.getByRole('region', { name: 'Профиль' })).queryByLabelText('Язык')).not.toBeInTheDocument();
    await toggleBlock('Голос и звонки');
    expect(within(screen.getByRole('region', { name: 'Голос и звонки' })).getByLabelText('Язык')).toBeVisible();
  });

  it('fits a phone: one column, nothing wider than the screen, touch-size controls, a compact save bar', async () => {
    stubViewportWidth(PHONE_WIDTH);
    backend();
    await renderApp('/personas/1');
    await blockButton('Профиль');

    const layout = document.querySelector('.touch-40') as HTMLElement;
    const columns = layout.className.split(/\s+/).filter((c) => c.includes('grid-cols-'));
    expect(columns).toEqual(['xl:grid-cols-[minmax(0,1fr)_24rem]']);
    expect(document.querySelector('[data-slot="persona-blocks"]')).toHaveClass('min-w-0');
    expect(document.querySelector('[data-slot="persona-preview"]')).toHaveClass('min-w-0');
    for (const section of document.querySelectorAll('[data-section]')) {
      expect(section.parentElement).toHaveClass('min-w-0');
    }

    for (const preview of document.querySelectorAll('[data-slot="long-text-preview"]')) {
      expect(preview).toHaveClass('whitespace-pre-wrap', '[overflow-wrap:anywhere]');
    }
    for (const el of layout.querySelectorAll('[class]')) {
      expect(el.getAttribute('class')).not.toMatch(/(^|\s)(min-)?w-\[\d+(px|rem)\]/);
    }

    for (const section of SECTIONS) {
      expect(screen.getByRole('button', { name: section.label })).toHaveClass('min-h-12', 'w-full');
    }
    expect(screen.getByLabelText('Активен').parentElement).toHaveClass('min-h-[40px]');

    const bar = document.querySelector('[data-slot="save-bar"]') as HTMLElement;
    expect(bar).toHaveClass('sticky', 'bottom-(--bottom-nav)', 'py-2');
    expect(bar.parentElement?.lastElementChild).toBe(bar);
    expect(within(bar).getByRole('button', { name: 'Сохранить персонажа' })).toBeVisible();
  });
});

describe('prompt versions in the «Промпт» block', () => {
  const version = (id: number, text: string, note = '') => ({
    id, text, author: 'Дима', note, created_at: `2026-11-1${id}T10:00:00Z`,
  });

  it('lists the versions newest first and restores an older one into the form', async () => {
    let items = [version(2, AGENT_PROMPT), version(1, 'Первая версия', 'версия на момент включения истории')];
    const log = backend({
      'GET /api/personas/1/prompt/versions': () => ({ items }),
      'POST /api/personas/1/prompt/versions/1/restore': () => {
        items = [version(3, 'Первая версия', 'восстановлена версия от 11.11.2026 10:00 UTC'), ...items];
        return { mode: 'unified', agent_prompt: 'Первая версия', items };
      },
    });
    await renderApp('/personas/1');
    await userEvent.click(await screen.findByText('Версии промпта (2)'));
    const rows = within(screen.getByRole('list', { name: 'Версии промпта' })).getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('текущая');
    expect(within(rows[0]).queryByRole('button', { name: 'Восстановить' })).not.toBeInTheDocument();
    await userEvent.click(within(rows[1]).getByRole('button', { name: 'Восстановить' }));
    await waitFor(() => expect(promptBox()).toHaveValue('Первая версия'));
    expect(await screen.findByText('Версии промпта (3)')).toBeInTheDocument();
    expect(unsaved()).toHaveTextContent('');          // the restored text is already saved
    expect(log.some((r) => r.method === 'PUT')).toBe(false);
  });
});
