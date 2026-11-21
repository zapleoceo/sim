import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from '@tanstack/react-router';
import { useEffect } from 'react';
import { ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Card, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { settingsApi } from '@/lib/api/settings';
import { qk } from '@/lib/query';
import { SettingField } from './setting-field';
import { AGENT_KEYS, groupAnchor, groupSettings, pickSettings, PROMPT_GROUPS, settingAnchor, settingFieldKey } from './settings-logic';

export const GUARD_GROUP = 'Охранник';
export const PASSPORT_GROUP = 'Паспортистка';
export const SUMMARY_GROUP = 'Саммари диалога';
export const AGENT_GROUPS = [GUARD_GROUP, PASSPORT_GROUP, SUMMARY_GROUP] as const;
const ROLES: Record<string, string> = {
  [GUARD_GROUP]: 'Проверяет ответ перед отправкой. Отклонённый текст возвращается на исправление. После этого промпта охранник получает правила из «Общие правила → Промпт · 4».',
  [PASSPORT_GROUP]: 'Обновляет паспорт собеседника: что известно о человеке и его запросе.',
  [SUMMARY_GROUP]: 'Сохраняет смысл старых сообщений, которые вышли из окна контекста.',
};

/** Where the texts of a «Промпт ·» group come from besides this page: each one is edited in one place. */
function PromptNote({ group }: { group: string }) {
  if (group === PROMPT_GROUPS.reply) return <p className="mb-4 text-sm text-muted-foreground">
    Модель сначала получает промпт персонажа (страница персонажа → «Промпт») и его распорядок, затем эти тексты в этом порядке.
  </p>;
  if (group === PROMPT_GROUPS.context) return <p className="mb-4 text-sm text-muted-foreground">
    Заголовки и правила блоков, которые код добавляет к каждому ответу после переписки. Сами данные (время, паспорт, саммари) считает код и помощники;
    инструкции паспорта и саммари — на странице <Link to="/agents" className="underline">«Помощники»</Link>.
  </p>;
  if (group === PROMPT_GROUPS.guard) return <p className="mb-4 text-sm text-muted-foreground">
    Идут после основного промпта охранника — он и модель охранника на странице <Link to="/agents" hash={settingAnchor('guard_prompt')} className="underline">«Помощники»</Link>.
  </p>;
  return null;
}

function SettingsView({ helpers = false }: { helpers?: boolean }) {
  const query = useQuery({ queryKey: qk.settings, queryFn: settingsApi.list });
  const hash = useLocation({ select: (location) => location.hash });
  const navigate = useNavigate();
  const groups = groupSettings(pickSettings(query.data ?? [], helpers ? AGENT_KEYS : undefined))
    .filter((group) => helpers === (AGENT_GROUPS as readonly string[]).includes(group.name));
  const current = groups.find((group) => groupAnchor(group.name) === hash
    || group.items.some((item) => settingAnchor(item.key) === hash));
  useEffect(() => {
    if (query.isSuccess && hash) document.getElementById(hash)?.scrollIntoView?.({ block: 'start' });
  }, [query.isSuccess, hash]);
  return <Page>
    <PageHeader title={helpers ? 'Помощники' : 'Общие правила'}
      subtitle={helpers ? 'Роли, модели и инструкции помощников персонажа.' : 'Всё, что модель получает кроме промпта персонажа: группы «Промпт ·» в порядке действия, затем общие параметры. Скрытых текстов в коде нет.'} />
    <p className="text-xs text-muted-foreground">Изменения применяются после сохранения поля. Черновики сохраняются при переходах внутри приложения, но не после закрытия вкладки.</p>
    {query.isPending && <div aria-busy="true"><Skeleton className="h-40 w-full" /></div>}
    {query.isError && <ErrorState onRetry={() => void query.refetch()} />}
    {query.isSuccess && !groups.length && <p role="status" className="rounded-lg border p-6 text-sm text-muted-foreground">Нет доступных настроек.</p>}
    <div className={'grid min-w-0 gap-4' + (groups.length > 1 ? ' lg:grid-cols-[14rem_minmax(0,1fr)]' : '')}>
      <aside className="min-w-0 lg:sticky lg:top-4 lg:self-start">
        {groups.length > 1 && <>
          <label className="flex flex-col gap-1 text-sm lg:hidden">Раздел настроек
            <select className="h-11 w-full rounded-md border bg-background px-3" value={current ? groupAnchor(current.name) : ''}
              onChange={(e) => { if (e.target.value) void navigate({ hash: e.target.value }); }}>
              <option value="" disabled>Выберите раздел</option>
              {groups.map((group) => <option key={group.name} value={groupAnchor(group.name)}>{group.name}</option>)}
            </select>
          </label>
          <nav aria-label="Группы настроек" className="hidden flex-col gap-1 lg:flex">
            {groups.map((group) => <Link key={group.name} to="." hash={groupAnchor(group.name)}
              aria-current={current?.name === group.name ? 'location' : undefined}
              className="rounded-md px-3 py-2 text-sm transition-colors hover:bg-accent focus-visible:ring-2 aria-[current=location]:bg-accent">
              {group.name}
            </Link>)}
          </nav>
        </>}
      </aside>
      <div className="flex min-w-0 flex-col gap-4">
        {groups.map((group) => <section key={group.name} id={groupAnchor(group.name)} aria-label={group.name} className="min-w-0 scroll-mt-4">
          <Card>
            <CardTitle>{group.name}</CardTitle>
            {helpers ? <p className="mb-4 text-sm text-muted-foreground">{ROLES[group.name]}</p> : <PromptNote group={group.name} />}
            <div className="flex min-w-0 flex-col gap-3">
              {group.items.map((item) => <div key={settingFieldKey(item)} id={settingAnchor(item.key)} className="min-w-0 scroll-mt-4">
                <SettingField item={item} />
              </div>)}
            </div>
          </Card>
        </section>)}
      </div>
    </div>
  </Page>;
}

export function SettingsPage() { return <SettingsView />; }
export function AgentsPage() { return <SettingsView helpers />; }
