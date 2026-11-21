import { useBlocker } from '@tanstack/react-router';
import { useConfirm } from '@/components/confirm';

/** The text of the «leave without saving?» dialog: which parts of the page are not saved. */
export function unsavedMessage(names: readonly string[]): string {
  return names.length > 0
    ? `Не сохранено: ${names.join(', ')}. Изменения пропадут.`
    : 'Изменения на этой странице не сохранены и пропадут.';
}

/**
 * Asks before leaving a form with unsaved changes: in-app navigation shows the confirm dialog
 * (naming the unsaved parts), closing or reloading the tab shows the browser prompt.
 * Sections of one page register in `UnsavedChangesProvider` instead (one dialog for the page).
 */
export function useUnsavedGuard(dirty: boolean, names: readonly string[] = []): void {
  const confirm = useConfirm();
  useBlocker({
    shouldBlockFn: async () =>
      !(await confirm({
        title: 'Уйти без сохранения?',
        message: unsavedMessage(names),
        confirmLabel: 'Уйти',
        danger: true,
      })),
    enableBeforeUnload: dirty,
    disabled: !dirty,
  });
}
