import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SECTIONS } from './persona-sections';
import { EMPTY_PERSONA } from './persona-schema';
import type { AppSettingItem, Persona } from '@/lib/models';
import { fakeApi, type FakeReply } from '@/test/fake-api';
import { blockButton, openLongText, toggleBlock } from '@/test/persona-blocks';
import { renderApp } from '@/test/render-app';
import { PHONE_WIDTH, stubViewportWidth } from '@/test/viewport';

const AGENT_PROMPT = 'Ты Марина Светлова.\n\nМне 45, я Марина.';

const MARINA: Persona = {
  ...EMPTY_PERSONA,
  id: 1,
  name: 'Марина',
  description: 'legacy description',
  routine: 'пн-пт 09:00-13:00 консультации',
  created_at: '2026-11-01T10:00:00Z',
};

function backend(overrides: Record<string, FakeReply> = {}) {
  return fakeApi({
    'GET /api/personas': { items: [MARINA] },
    'GET /api/strategies': { items: [] },
    'GET /api/models': { items: [] },
    'GET /api/personas/1/media': { items: [] },
    'GET /api/settings': { items: [], fixed: { persona: '', guard: '' } },
    'GET /api/personas/1/prompt': {
      agent_prompt: AGENT_PROMPT,
      system: 'системное сообщение', fixed: '', strategy_id: 1, context: '- Тебе 45',
      parts: [{ key: 'agent_prompt', title: 'Единый промпт агента', text: AGENT_PROMPT, source: 'persona', field: 'agent_prompt' }],
      rules: [{ key: 'call', title: 'Правило звонков', text: 'ты ИИ-ассистент' }],
    },
    'PUT /api/personas/1': (r: { body: unknown }) => ({ ...MARINA, ...(r.body as object) }),
    ...overrides,
  });
}

const savedBody = (log: ReturnType<typeof backend>): Record<string, unknown> | undefined =>
  log.find((r) => r.method === 'PUT' && r.path === '/api/personas/1')?.body as Record<string, unknown> | undefined;

/** The note of the save bar about unsaved edits (toasts are status regions too, so it is taken by its place). */
const unsaved = (): HTMLElement => document.querySelector('[data-slot="save-bar"] [role="status"]') as HTMLElement;

async function save(): Promise<void> {
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить персонажа' }));
}

describe('persona page layout', () => {
  it('groups the page into blocks that fold: only «Профиль» is open at first', async () => {
    backend();
    await renderApp('/personas/1');
    expect(await blockButton('Профиль')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Имя')).toBeVisible();
    for (const section of SECTIONS.filter((s) => s.id !== 'profile')) {
      expect(screen.getByRole('button', { name: section.label })).toHaveAttribute('aria-expanded', 'false');
      expect(document.getElementById(`${section.id}-panel`)).not.toBeVisible();
    }
    const order = [...document.querySelectorAll('[data-section]')].map((el) => el.getAttribute('data-section'));
    expect(order).toEqual(['profile', 'routine', 'voice', 'strategy', 'prompt', 'media', 'shared', 'assembled']);

    await toggleBlock('Голос и звонки');
    expect(screen.getByRole('button', { name: 'Голос и звонки' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Принимать звонки')).toBeVisible();
    expect(screen.getByLabelText('Имя')).toBeVisible();

    await toggleBlock('Голос и звонки');
    expect(screen.getByLabelText('Принимать звонки')).not.toBeVisible();
    await toggleBlock('Профиль');
    expect(screen.getByLabelText('Имя')).not.toBeVisible();
  });

  it('shows the agent prompt in its own section with a dedicated save', async () => {
    backend();
    await renderApp('/personas/1');
    await toggleBlock('Промпт');
    const section = within(screen.getByRole('region', { name: 'Промпт' }));
    const textarea = section.getByRole('textbox', { name: 'Промпт' });
    expect(textarea).toBeVisible();
    expect(textarea).toHaveValue(AGENT_PROMPT);
    expect(section.getByRole('button', { name: 'Сохранить промпт' })).toBeVisible();
  });

  it('keeps the context model section folded until its header is pressed', async () => {
    backend();
    await renderApp('/personas/1');
    const header = await blockButton('Контекст модели');
    expect(header).toHaveAttribute('aria-expanded', 'false');
    expect(await screen.findByText(AGENT_PROMPT)).not.toBeVisible();

    await userEvent.click(header);
    const assembled = within(screen.getByRole('region', { name: 'Контекст модели' }));
    expect(assembled.getByText(AGENT_PROMPT)).toBeVisible();
    expect(assembled.getByText('ты ИИ-ассистент')).not.toBeVisible();
    await userEvent.click(assembled.getByText('Технические правила и обработка'));
    const fixed = within(assembled.getByRole('group', { name: 'Фиксированные правила' }));
    expect(fixed.getByText('ты ИИ-ассистент')).toBeVisible();
    expect(fixed.queryByRole('textbox')).not.toBeInTheDocument();
    expect(fixed.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows the routine long text in its full editor', async () => {
    backend();
    await renderApp('/personas/1');
    await toggleBlock('Распорядок');
    expect(await openLongText('Распорядок недели')).toHaveValue('пн-пт 09:00-13:00 консультации');
  });

  it.each(['save', 'reset'] as const)('locks shared text while %s is pending, then shows the returned value', async (operation) => {
    const setting: AppSettingItem = {
      key: 'output_contract', group: 'Ответ', label: 'Формат ответа', help: '', kind: 'text',
      min: null, max: null, value: 'свой формат', default: 'исходный формат', is_default: false,
      updated_at: null, updated_by_id: null,
    };
    let finish!: (item: AppSettingItem) => void;
    const pending = new Promise<AppSettingItem>((resolve) => { finish = resolve; });
    backend({
      'GET /api/settings': { items: [setting], fixed: { persona: '', guard: '' } },
      'PUT /api/settings/output_contract': () => pending,
      'DELETE /api/settings/output_contract': () => pending,
    });
    await renderApp('/personas/1');
    await toggleBlock('Общие инструкции');
    const region = within(document.getElementById('setting-output_contract') as HTMLElement);
    const input = region.getByRole('textbox', { name: 'Формат ответа' });
    await userEvent.clear(input);
    await userEvent.type(input, 'новый формат');
    await userEvent.click(region.getByRole('button', { name: operation === 'save' ? 'Сохранить' : 'Вернуть по умолчанию' }));
    await waitFor(() => expect(input).toBeDisabled());
    expect(region.getByRole('button', { name: 'Редактировать: Формат ответа' })).toBeDisabled();
    await userEvent.type(input, ' потерянная правка');
    expect(input).toHaveValue('новый формат');
    const returned = operation === 'save' ? 'новый формат' : 'исходный формат';
    finish({ ...setting, value: returned, is_default: operation === 'reset', updated_at: '2026-11-14T21:00:00Z' });
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Формат ответа' })).toBeEnabled());
    expect(screen.getByRole('textbox', { name: 'Формат ответа' })).toHaveValue(returned);
  });

  it('preserves hidden description and prohibitions when saving the persona form', async () => {
    const log = backend();
    await renderApp('/personas/1');
    const name = await screen.findByLabelText('Имя');
    await userEvent.clear(name);
    await userEvent.type(name, 'Марина Светлова');
    await save();
    await waitFor(() => expect(savedBody(log)).toBeDefined());
    expect(savedBody(log)).toMatchObject({
      name: 'Марина Светлова',
      description: MARINA.description,
      routine: MARINA.routine,
    });
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
    expect(savedBody(log)).toMatchObject({ name: 'Марина Светлова', routine: 'пн-пт 09:00-13:00 консультации' });
    await waitFor(() => expect(unsaved()).toBeEmptyDOMElement());
  });

  it('shows language in the voice section, not in the profile', async () => {
    backend();
    await renderApp('/personas/1');
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
    await toggleBlock('Контекст модели');
    for (const piece of screen.getByRole('region', { name: 'Контекст модели' }).querySelectorAll('pre')) {
      expect(piece).toHaveClass('whitespace-pre-wrap', '[overflow-wrap:anywhere]');
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
