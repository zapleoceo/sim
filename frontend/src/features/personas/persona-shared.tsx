import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ErrorState } from '@/components/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { settingsApi } from '@/lib/api/settings';
import { qk } from '@/lib/query';
import { SettingField } from '../settings/setting-field';
import { pickSettings, settingFieldKey } from '../settings/settings-logic';
import { GuardControl } from '../settings/settings-page';
import { settingAnchor } from './persona-prompt-logic';

/** The shared settings that take part in every persona's reply, in the order they act. */
const GROUPS: readonly { title: string; keys: readonly string[]; footer?: ReactNode }[] = [
  { title: 'Ответ персонажа', keys: ['output_contract', 'retry_nudge', 'history_limit', 'reply_debounce_s'] },
  { title: 'Стиль сообщений', keys: ['typo_percent', 'lowercase_percent'] },
  { title: 'Охранник', keys: ['guard_prompt', 'guard_model'], footer: <GuardControl /> },
  { title: 'Паспортистка', keys: ['passport_prompt', 'passport_model'] },
  { title: 'Кружочки ИИ', keys: ['video_note_owner_telegram_id'] },
];

/**
 * The shared instructions mixed into this persona's replies, editable in place. They are the same
 * settings as on «Общие правила» and «Агенты»: a change here changes them for every persona.
 */
export function PersonaShared() {
  const query = useQuery({ queryKey: qk.settings, queryFn: settingsApi.list });

  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError) return <ErrorState onRetry={() => void query.refetch()} />;

  return (
    <div className="flex flex-col gap-6">
      {GROUPS.map((group) => (
        <div key={group.title} role="group" aria-label={group.title} className="flex flex-col gap-4">
          <h3 className="text-sm font-medium">{group.title}</h3>
          {pickSettings(query.data, group.keys).map((item) => (
            <div key={settingFieldKey(item)} id={settingAnchor(item.key)} className="scroll-mt-4">
              <SettingField item={item} />
            </div>
          ))}
          {group.footer}
        </div>
      ))}
    </div>
  );
}
