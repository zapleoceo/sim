import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SECTIONS } from './persona-sections';
import { EMPTY_PERSONA } from './persona-schema';
import type { Persona } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { blockButton, openLongText, toggleBlock, typeLongText } from '@/test/persona-blocks';
import { renderApp } from '@/test/render-app';
import { PHONE_WIDTH, stubViewportWidth } from '@/test/viewport';

/** A real-size prompt: many lines, blank lines, indentation, a trailing space and a line break at the end. */
const LONG_PROMPT = `${Array.from({ length: 120 }, (_, i) => `Строка ${i + 1}: ты Марина, пиши коротко и по делу.  `).join('\n')}\n\n  - не обещай доход\n`;

const MARINA: Persona = {
  ...EMPTY_PERSONA,
  id: 1,
  name: 'Марина',
  description: LONG_PROMPT,
  routine: 'пн-пт 09:00-13:00 консультации',
  created_at: '2026-11-01T10:00:00Z',
};

function backend() {
  return fakeApi({
    'GET /api/personas': { items: [MARINA] },
    'GET /api/strategies': { items: [] },
    'GET /api/models': { items: [] },
    'GET /api/personas/1/media': { items: [] },
    'GET /api/settings': { items: [], fixed: { persona: '', guard: '' } },
    'GET /api/personas/1/prompt': {
      system: 'системное сообщение', fixed: '', strategy_id: 1, context: '- Тебе 45',
      parts: [{ key: 'description', title: 'Кто ты', text: 'текст персонажа', source: 'persona', field: 'description' }],
      rules: [{ key: 'call', title: 'Правило звонков', text: 'ты ИИ-ассистент' }],
    },
    'PUT /api/personas/1': (r: { body: unknown }) => ({ ...MARINA, ...(r.body as object) }),
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
    // the blocks stand in page order, from what is edited most often to what is edited rarely
    const order = [...document.querySelectorAll('[data-section]')].map((el) => el.getAttribute('data-section'));
    expect(order).toEqual(['profile', 'prompt', 'routine', 'voice', 'strategy', 'media', 'shared', 'assembled']);

    await toggleBlock('Голос и звонки');
    expect(screen.getByRole('button', { name: 'Голос и звонки' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Принимать звонки')).toBeVisible();
    expect(screen.getByLabelText('Имя')).toBeVisible();   // opening one block does not close another

    await toggleBlock('Голос и звонки');
    expect(screen.getByLabelText('Принимать звонки')).not.toBeVisible();
    await toggleBlock('Профиль');
    expect(screen.getByLabelText('Имя')).not.toBeVisible();
  });

  it('keeps the assembled prompt folded until its header is pressed', async () => {
    backend();
    await renderApp('/personas/1');
    const header = await blockButton('Итоговый промпт');
    expect(header).toHaveAttribute('aria-expanded', 'false');
    expect(await screen.findByText('текст персонажа')).not.toBeVisible();

    await userEvent.click(header);
    const assembled = within(screen.getByRole('region', { name: 'Итоговый промпт' }));
    expect(assembled.getByText('текст персонажа')).toBeVisible();
    // Technical text is folded, not removed or converted to an editable instruction.
    expect(assembled.getByText('ты ИИ-ассистент')).not.toBeVisible();
    await userEvent.click(assembled.getByText('Технические правила и обработка'));
    // the fixed rules are read-only, with the text the backend gave
    const fixed = within(assembled.getByRole('group', { name: 'Фиксированные правила' }));
    expect(fixed.getByText('ты ИИ-ассистент')).toBeVisible();
    expect(fixed.queryByRole('textbox')).not.toBeInTheDocument();
    expect(fixed.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows directly editable compact text and opens the same whole text in the full editor', async () => {
    backend();
    await renderApp('/personas/1');
    await toggleBlock('Характер и промпт');
    const field = document.getElementById('persona-description') as HTMLElement;
    const inline = within(field).getByRole('textbox', { name: 'Промпт персонажа' });
    expect(inline).toBeEnabled();
    expect(inline).not.toHaveAttribute('readonly');
    expect(inline).toHaveValue(LONG_PROMPT);
    expect(inline).toHaveAttribute('rows', '4');

    const editor = await openLongText('Промпт персонажа');
    expect(editor).toHaveValue(LONG_PROMPT);   // the whole text, not the preview
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Готово' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // the other long texts of the form open the same way
    await toggleBlock('Распорядок');
    expect(await openLongText('Распорядок недели')).toHaveValue('пн-пт 09:00-13:00 консультации');
  });

  it('saves direct inline edits without cutting other long fields', async () => {
    const log = backend();
    await renderApp('/personas/1');
    await toggleBlock('Характер и промпт');
    const field = document.getElementById('persona-prohibitions') as HTMLElement;
    await userEvent.type(within(field).getByRole('textbox', { name: 'Запреты' }), 'не обещать доход');
    await toggleBlock('Характер и промпт');
    await save();
    await waitFor(() => expect(savedBody(log)).toBeDefined());
    expect(savedBody(log)).toMatchObject({ prohibitions: 'не обещать доход', description: LONG_PROMPT, routine: MARINA.routine });
    await toggleBlock('Характер и промпт');
    expect(within(field).getByRole('textbox', { name: 'Запреты' })).toHaveValue('не обещать доход');
  });

  it('saves the prompt exactly as it is when another field is edited in a folded page', async () => {
    const log = backend();
    await renderApp('/personas/1');
    const name = await screen.findByLabelText('Имя');
    await userEvent.clear(name);
    await userEvent.type(name, 'Марина Светлова');
    expect(unsaved()).toHaveTextContent('Есть несохранённые правки');
    await toggleBlock('Профиль');   // a folded block still belongs to the form
    await save();

    await waitFor(() => expect(savedBody(log)).toBeDefined());
    expect(savedBody(log)).toMatchObject({ name: 'Марина Светлова', routine: 'пн-пт 09:00-13:00 консультации' });
    expect(savedBody(log)?.description).toBe(LONG_PROMPT);
    await waitFor(() => expect(unsaved()).toBeEmptyDOMElement());
  });

  it('saves the prompt edited in the full editor without cutting it', async () => {
    const log = backend();
    await renderApp('/personas/1');
    await toggleBlock('Характер и промпт');
    await typeLongText('Промпт персонажа', 'Новая строка в конце');
    const preview = document.querySelector('#persona-description [data-slot="long-text-preview"]');
    expect(preview).toHaveValue(`${LONG_PROMPT}Новая строка в конце`);
    expect(log.some((r) => r.method === 'PUT')).toBe(false);   // the dialog edits the form, «Сохранить» saves it
    expect(unsaved()).toHaveTextContent('Есть несохранённые правки');

    await save();
    await waitFor(() => expect(savedBody(log)).toBeDefined());
    expect(savedBody(log)?.description).toBe(`${LONG_PROMPT}Новая строка в конце`);
  });

  it('keeps the text typed in the full editor when the dialog is dismissed with Escape', async () => {
    const log = backend();
    await renderApp('/personas/1');
    await toggleBlock('Характер и промпт');
    await userEvent.type(await openLongText('Запреты'), 'не обсуждать политику');
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await save();
    await waitFor(() => expect(savedBody(log)).toBeDefined());
    expect(savedBody(log)).toMatchObject({ prohibitions: 'не обсуждать политику', description: LONG_PROMPT });
  });

  it('fits a phone: one column, nothing wider than the screen, touch-size controls, a compact save bar', async () => {
    stubViewportWidth(PHONE_WIDTH);
    backend();
    await renderApp('/personas/1');
    await blockButton('Профиль');

    // one column below `xl`; both columns may shrink (min-w-0), so long content cannot stretch the page
    const layout = document.querySelector('.touch-40') as HTMLElement;
    const columns = layout.className.split(/\s+/).filter((c) => c.includes('grid-cols-'));
    expect(columns).toEqual(['xl:grid-cols-[minmax(0,1fr)_24rem]']);
    expect(document.querySelector('[data-slot="persona-blocks"]')).toHaveClass('min-w-0');
    expect(document.querySelector('[data-slot="persona-preview"]')).toHaveClass('min-w-0');
    for (const section of document.querySelectorAll('[data-section]')) {
      expect(section.parentElement).toHaveClass('min-w-0');
    }

    // long words wrap in previews and in the pieces of the assembled prompt; nothing has a fixed width in px
    for (const preview of document.querySelectorAll('[data-slot="long-text-preview"]')) {
      expect(preview).toHaveClass('whitespace-pre-wrap', '[overflow-wrap:anywhere]');
    }
    await toggleBlock('Итоговый промпт');
    for (const piece of screen.getByRole('region', { name: 'Итоговый промпт' }).querySelectorAll('pre')) {
      expect(piece).toHaveClass('whitespace-pre-wrap', '[overflow-wrap:anywhere]');
    }
    for (const el of layout.querySelectorAll('[class]')) {
      expect(el.getAttribute('class')).not.toMatch(/(^|\s)(min-)?w-\[\d+(px|rem)\]/);
    }

    // block headers and the switch rows are touch targets; buttons, inputs and selects get 40 px from `.touch-40`
    for (const section of SECTIONS) {
      expect(screen.getByRole('button', { name: section.label })).toHaveClass('min-h-12', 'w-full');
    }
    expect(screen.getByLabelText('Активен').parentElement).toHaveClass('min-h-[40px]');

    // the save bar sticks above the bottom tab bar and is the last, thin row of the form
    const bar = document.querySelector('[data-slot="save-bar"]') as HTMLElement;
    expect(bar).toHaveClass('sticky', 'bottom-(--bottom-nav)', 'py-2');
    expect(bar.parentElement?.lastElementChild).toBe(bar);
    expect(within(bar).getByRole('button', { name: 'Сохранить персонажа' })).toBeVisible();
  });
});
