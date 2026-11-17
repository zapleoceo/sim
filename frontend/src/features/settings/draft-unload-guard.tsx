import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { SETTINGS_DRAFTS, type SettingDraft } from './settings-logic';

/** Lives with the app, not a route. Private drafts stay memory-only. */
export function SettingsDraftUnloadGuard() {
  const client = useQueryClient();
  useEffect(() => {
    const hasDraft = (): boolean => client.getQueriesData<SettingDraft | null>({ queryKey: SETTINGS_DRAFTS })
      .some(([, draft]) => Boolean(draft));
    const warn = (event: BeforeUnloadEvent): void => {
      if (hasDraft()) { event.preventDefault(); event.returnValue = ''; }
    };
    let listening = false;
    const sync = (): void => {
      const needed = hasDraft();
      if (needed && !listening) window.addEventListener('beforeunload', warn);
      if (!needed && listening) window.removeEventListener('beforeunload', warn);
      listening = needed;
    };
    const unsubscribe = client.getQueryCache().subscribe(sync);
    sync();
    return () => { unsubscribe(); window.removeEventListener('beforeunload', warn); };
  }, [client]);
  return null;
}
