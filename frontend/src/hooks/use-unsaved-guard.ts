import { useBlocker } from '@tanstack/react-router';
import { useConfirm } from '@/components/confirm';

/**
 * Asks before leaving a form with unsaved changes: in-app navigation shows the confirm dialog,
 * closing or reloading the tab shows the browser prompt.
 */
export function useUnsavedGuard(dirty: boolean): void {
  const confirm = useConfirm();
  useBlocker({
    shouldBlockFn: async () =>
      !(await confirm({
        title: 'Уйти без сохранения?',
        message: 'Изменения на этой странице не сохранены и пропадут.',
        confirmLabel: 'Уйти',
        danger: true,
      })),
    enableBeforeUnload: dirty,
    disabled: !dirty,
  });
}
