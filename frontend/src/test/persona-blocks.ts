import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/** The header button of a persona page block (it waits for the page to load). */
export function blockButton(name: string): Promise<HTMLElement> {
  return screen.findByRole('button', { name });
}

/** Unfolds (or folds) a block of the persona page by its header. */
export async function toggleBlock(name: string): Promise<void> {
  await userEvent.click(await blockButton(name));
}

/** Opens the full editor of a long text by its «Редактировать» and returns the dialog's text box. */
export async function openLongText(label: string): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole('button', { name: `Редактировать: ${label}` }));
  return within(await screen.findByRole('dialog', { name: label })).getByRole('textbox', { name: label });
}

/** Types at the end of a long text in its full editor and closes it with «Готово». */
export async function typeLongText(label: string, text: string): Promise<void> {
  await userEvent.type(await openLongText(label), text);
  await userEvent.click(within(screen.getByRole('dialog', { name: label })).getByRole('button', { name: 'Готово' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
}
