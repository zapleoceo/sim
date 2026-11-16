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

/** Settings of one or more groups, a card per group; every field is open, no hidden sections. */
function SettingsGroups({ only, except, keys, groupFooters }: {
  /** Show only these groups (by name). */
  only?: readonly string[];
  /** Hide these groups (by name). */
  except?: readonly string[];
  /** Show only these settings, in this order (other keys of the group stay in the DB). */
  keys?: readonly string[];
  /** Shown at the end of a group's card, by group name (read-only notes of that group). */
  groupFooters?: Readonly<Record<string, ReactNode>>;
}) {
  const query = useQuery({ queryKey: qk.settings, queryFn: settingsApi.list });
  const groups = groupSettings(pickSettings(query.data ?? [], keys)).filter(
    (g) => (!only || only.includes(g.name)) && !(except ?? []).includes(g.name),
  );

  return (
    <>
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
    </>
  );
}

/** The agents: each has its own group, model and instruction (the guard, the passport keeper, the summary). */
export const GUARD_GROUP = 'Охранник';
export const PASSPORT_GROUP = 'Паспортистка';
export const SUMMARY_GROUP = 'Саммари диалога';
export const AGENT_GROUPS = [GUARD_GROUP, PASSPORT_GROUP, SUMMARY_GROUP] as const;

/** Shared rules for every persona (the agents are on «Персонажи»). */
export function SettingsPage() {
  return (
    <Page width="narrow">
      <PageHeader
        title="Общие правила"
        subtitle="Служебные промпты и параметры, общие для всех персонажей. Промпт самого персонажа и агенты — на странице «Персонажи». Хранятся в базе: деплой их не сбрасывает."
      />
      <SettingsGroups except={AGENT_GROUPS} />
    </Page>
  );
}

/** What the agents block shows: each agent's model and its one instruction; Broker routes stay a backend fallback. */
export const AGENT_KEYS = [
  'guard_model', 'guard_prompt', 'passport_model', 'passport_prompt', 'summary_model', 'summary_prompt',
] as const;

export function GuardControl() {
  const fixed = useQuery({ queryKey: qk.settingsFixed, queryFn: settingsApi.fixed });
  return fixed.data?.guard ? <FixedPrompt text={fixed.data.guard} where="после промпта охранника" /> : null;
}

export const AGENTS_TITLE = 'Агенты (общие для всех персонажей)';

/**
 * The agents around every persona, shown on «Персонажи»: the guard checks every reply before it is sent,
 * the passport keeper updates what the persona knows about each lead, the summary keeps the memory of old
 * messages. Each has its own model and instruction.
 */
export function AgentSettings() {
  return (
    <section aria-labelledby="agents-h" className="flex flex-col gap-4">
      <header className="min-w-0">
        <h2 id="agents-h" className="text-base font-semibold sm:text-lg">{AGENTS_TITLE}</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Помощники каждого персонажа: охранник проверяет ответ перед отправкой, паспортистка обновляет паспорт
          собеседника, саммари хранит память о старых сообщениях. У каждого своя модель и инструкция.
        </p>
      </header>
      <SettingsGroups only={AGENT_GROUPS} keys={AGENT_KEYS} groupFooters={{ [GUARD_GROUP]: <GuardControl /> }} />
    </section>
  );
}
