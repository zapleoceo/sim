import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ErrorState } from '@/components/empty-state';
import { FixedPrompt } from '@/components/fixed-prompt';
import { Page, PageHeader } from '@/components/page';
import { Card, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { settingsApi } from '@/lib/api/settings';
import { qk } from '@/lib/query';
import { SettingField } from './setting-field';
import { groupSettings, pickSettings, settingFieldKey } from './settings-logic';

/** Settings of one or more groups on one page; every field is open, no hidden sections. */
function SettingsView({ title, subtitle, only, except, keys, footer, groupFooters }: {
  title: string;
  subtitle: string;
  /** Show only these groups (by name). */
  only?: readonly string[];
  /** Hide these groups (by name). */
  except?: readonly string[];
  /** Show only these settings, in this order (other keys of the group stay in the DB). */
  keys?: readonly string[];
  /** Shown after the settings (read-only notes). */
  footer?: ReactNode;
  /** Shown at the end of a group's card, by group name (read-only notes of that group). */
  groupFooters?: Readonly<Record<string, ReactNode>>;
}) {
  const query = useQuery({ queryKey: qk.settings, queryFn: settingsApi.list });
  const groups = groupSettings(pickSettings(query.data ?? [], keys)).filter(
    (g) => (!only || only.includes(g.name)) && !(except ?? []).includes(g.name),
  );

  return (
    <Page width="narrow">
      <PageHeader title={title} subtitle={subtitle} />
      {query.isPending && (
        <div className="flex flex-col gap-4" aria-busy="true">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      )}
      {query.isError && <ErrorState onRetry={() => void query.refetch()} />}
      {groups.map((g) => (
        <section key={g.name} aria-label={g.name}>
          <Card>
          <CardTitle>{g.name}</CardTitle>
          <div className="flex flex-col gap-6">
            {g.items.map((item) => (
              <SettingField key={settingFieldKey(item)} item={item} />
            ))}
            {groupFooters?.[g.name]}
          </div>
          </Card>
        </section>
      ))}
      {footer}
    </Page>
  );
}

/** The agents: each has its own group, model and instruction (the guard, the passport keeper). */
export const GUARD_GROUP = 'Охранник';
export const PASSPORT_GROUP = 'Паспортистка';
export const AGENT_GROUPS = [GUARD_GROUP, PASSPORT_GROUP] as const;

/** Shared rules for every persona (the agents have their own page). */
export function SettingsPage() {
  return (
    <SettingsView
      title="Общие правила"
      subtitle="Служебные промпты и параметры, общие для всех персонажей. Промпт самого персонажа на его странице. Хранятся в базе: деплой их не сбрасывает."
      except={AGENT_GROUPS}
    />
  );
}

/** What the agents page shows: each agent's model and its one instruction; Broker routes stay a backend fallback. */
export const AGENT_KEYS = ['guard_model', 'guard_prompt', 'passport_model', 'passport_prompt'] as const;

export function GuardControl() {
  const fixed = useQuery({ queryKey: qk.settingsFixed, queryFn: settingsApi.fixed });
  return fixed.data?.guard ? <FixedPrompt text={fixed.data.guard} where="после промпта охранника" /> : null;
}

/**
 * The agents around the persona: the guard checks every reply before it is sent; the passport
 * keeper updates what the persona knows about each lead after every reply.
 */
export function AgentsPage() {
  return (
    <SettingsView
      title="Агенты"
      subtitle="Помощники персонажа, у каждого своя модель и своя инструкция. Охранник проверяет каждый ответ перед отправкой. Паспортистка после каждого ответа обновляет паспорт собеседника: что персонаж о нём знает."
      only={AGENT_GROUPS}
      keys={AGENT_KEYS}
      groupFooters={{ [GUARD_GROUP]: <GuardControl /> }}
    />
  );
}
