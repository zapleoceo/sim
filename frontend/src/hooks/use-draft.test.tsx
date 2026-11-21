import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { UnsavedChangesProvider, uniqueNames } from '@/components/unsaved-changes';
import { sameJson } from '@/lib/utils';
import { renderWithApp } from '@/test/render';
import { useDraft } from './use-draft';
import { unsavedMessage } from './use-unsaved-guard';

interface Card { city: string; text: string }

/** A form over a server value that a «live update» button replaces. */
function LiveForm({ guardName, initial = { city: 'Киев', text: 'старый текст' } }: { guardName?: string; initial?: Card }) {
  const [server, setServer] = useState<Card>(initial);
  const { draft, setDraft, baseline, dirty, reset, commit } = useDraft(server, { guardName });
  return (
    <section aria-label={guardName ?? 'Форма'}>
      <input aria-label="Город" value={draft.city} onChange={(e) => setDraft({ ...draft, city: e.target.value })} />
      <output aria-label="Текст">{draft.text}</output>
      <output aria-label="База">{baseline.city}/{baseline.text}</output>
      <output aria-label="Состояние">{dirty ? 'изменено' : 'сохранено'}</output>
      <button type="button" onClick={() => setServer({ city: 'Львов', text: 'новый текст' })}>живое обновление</button>
      <button type="button" onClick={reset}>отменить</button>
      <button type="button" onClick={() => commit(draft)}>сохранено сервером</button>
      <button type="button" onClick={() => commit({ ...draft, text: 'с сервера' }, (d) => ({ ...d, city: `${d.city}!` }))}>
        сохранено с правкой
      </button>
    </section>
  );
}

const out = (name: string) => screen.getByLabelText(name).textContent;

describe('useDraft', () => {
  it('an untouched draft follows the server copy', async () => {
    await renderWithApp(<LiveForm />);
    await userEvent.click(screen.getByRole('button', { name: 'живое обновление' }));
    expect(screen.getByLabelText('Город')).toHaveValue('Львов');
    expect(out('Текст')).toBe('новый текст');
    expect(out('Состояние')).toBe('сохранено');
  });

  it('an edited draft keeps the edit and its baseline; «Отменить» brings the newest server copy', async () => {
    await renderWithApp(<LiveForm />);
    const city = screen.getByLabelText('Город');
    await userEvent.type(city, ' центр');
    await userEvent.click(screen.getByRole('button', { name: 'живое обновление' }));

    expect(city).toHaveValue('Киев центр');
    expect(out('Текст')).toBe('старый текст');          // nothing the operator did not type changes
    expect(out('База')).toBe('Киев/старый текст');      // the save diffs against the copy the edit started from
    expect(out('Состояние')).toBe('изменено');

    await userEvent.click(screen.getByRole('button', { name: 'отменить' }));
    expect(city).toHaveValue('Львов');
    expect(out('Состояние')).toBe('сохранено');
  });

  it('commit makes the saved value the baseline; rebase keeps the other edits', async () => {
    await renderWithApp(<LiveForm />);
    await userEvent.type(screen.getByLabelText('Город'), '-2');
    await userEvent.click(screen.getByRole('button', { name: 'сохранено сервером' }));
    expect(out('База')).toBe('Киев-2/старый текст');
    expect(out('Состояние')).toBe('сохранено');

    await userEvent.click(screen.getByRole('button', { name: 'сохранено с правкой' }));
    expect(out('База')).toBe('Киев-2/с сервера');
    expect(screen.getByLabelText('Город')).toHaveValue('Киев-2!');
    expect(out('Состояние')).toBe('изменено');
  });

  it('compares plain data by value', () => {
    expect(sameJson({ a: [1] }, { a: [1] })).toBe(true);
    expect(sameJson({ a: [1] }, { a: [2] })).toBe(false);
  });
});

describe('UnsavedChangesProvider', () => {
  it('names every unsaved section in one dialog', async () => {
    const { router } = await renderWithApp(
      <UnsavedChangesProvider>
        <LiveForm guardName="Агенты" />
        <LiveForm guardName="Мир" />
        <LiveForm guardName="Канон" />
      </UnsavedChangesProvider>,
    );
    await userEvent.type(within(screen.getByRole('region', { name: 'Агенты' })).getByLabelText('Город'), '!');
    await userEvent.type(within(screen.getByRole('region', { name: 'Мир' })).getByLabelText('Город'), '!');

    act(() => void router.navigate({ to: '/chats' }));

    const dialogs = await screen.findAllByRole('alertdialog');
    expect(dialogs).toHaveLength(1);
    expect(dialogs[0]).toHaveTextContent('Не сохранено: Агенты, Мир. Изменения пропадут.');
    await userEvent.click(within(dialogs[0]!).getByRole('button', { name: 'Отмена' }));
    expect(router.state.location.pathname).toBe('/');
  });

  it('lists each name once, in the order they became unsaved', () => {
    expect(uniqueNames(['День', 'Мир', 'День'])).toEqual(['День', 'Мир']);
    expect(unsavedMessage([])).toBe('Изменения на этой странице не сохранены и пропадут.');
  });
});
