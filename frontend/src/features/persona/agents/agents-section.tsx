import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCcw, Save } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { ErrorState, InlineError } from '@/components/empty-state';
import { Field, Section } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';
import { errorMessage } from '@/lib/api';
import { qk } from '@/lib/query';
import type { AgentRegistry, AgentSettings, BrokerModel, PersonaAgents } from '@/lib/types';
import { agentApi, personaApi } from '../api';
import { usePersonaId } from '../persona-scope';
import { AgentCard } from './agent-card';
import { type AgentsDraft, defaultSettings, draftErrors, effectiveModel, sameDraft, toDraft, toPatch } from './agents-logic';
import { AttentionBlock } from './attention-block';
import { ATTENTION_AGENT } from './attention-logic';

const GUARD = 'guard';

/** Every agent of the persona grouped as in the registry; one draft, one Save. */
export function AgentsEditor({ registry, saved, models }: { registry: AgentRegistry; saved: PersonaAgents; models: readonly BrokerModel[] }) {
  const queryClient = useQueryClient();
  const personaId = usePersonaId();
  const confirm = useConfirm();
  const [baseline, setBaseline] = useState(() => toDraft(saved));
  const [draft, setDraft] = useState<AgentsDraft>(baseline);
  const [errors, setErrors] = useState<string[]>([]);
  const dirty = !sameDraft(draft, baseline);
  useUnsavedGuard(dirty);

  const adopt = (next: PersonaAgents) => queryClient.setQueryData(qk.agents(personaId), next);
  const save = useMutation({
    mutationFn: (next: AgentsDraft) => agentApi.save(personaId, toPatch(next, baseline)),
    onSuccess: (result) => {
      adopt(result);
      const next = toDraft(result);
      setBaseline(next);
      setDraft(next);
      toast.success('Агенты сохранены');
    },
  });
  const reset = useMutation({
    mutationFn: (key: string) => agentApi.reset(personaId, key),
    onSuccess: (result, key) => {
      adopt(result);
      const next = toDraft(result);
      setBaseline(next);
      // The other agents keep their unsaved edits; «Внимание» resets its settings too.
      setDraft((d) => ({
        ...d,
        agents: { ...d.agents, [key]: next.agents[key] },
        attention: key === ATTENTION_AGENT ? next.attention : d.attention,
      }));
      toast.success('Агент вернулся к настройкам по умолчанию');
    },
  });

  const setAgent = (key: string) => (settings: AgentSettings) =>
    setDraft((d) => ({ ...d, agents: { ...d.agents, [key]: settings } }));
  const askReset = async (key: string, title: string) => {
    const ok = await confirm({
      title: `${title}: всё по умолчанию?`,
      message: key === ATTENTION_AGENT
        ? 'Модель, ключи, промт, max_tokens, режим, темп и переключатели станут стандартными. Сохраняется сразу.'
        : 'Модель, ключи, промт и max_tokens этого агента станут стандартными. Сохраняется сразу.',
      confirmLabel: 'Сбросить',
    });
    if (ok) reset.mutate(key);
  };

  return (
    <div className="flex flex-col gap-5">
      {registry.groups.map((group) => (
        <section key={group.key} aria-labelledby={`agents-group-${group.key}`} className="flex flex-col gap-3">
          <header>
            <h3 id={`agents-group-${group.key}`} className="text-sm font-semibold">{group.title}</h3>
            <p className="text-xs text-muted-foreground">{group.description}</p>
          </header>
          <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
            {group.agents.map((key) => {
              const spec = registry.agents.find((a) => a.key === key);
              if (!spec) return null;
              return (
                <AgentCard
                  key={key}
                  spec={spec}
                  specs={registry.agents}
                  settings={draft.agents[key] ?? defaultSettings(spec)}
                  effective={effectiveModel(draft, registry.agents, key)}
                  models={models}
                  tiers={registry.key_tiers}
                  tokensRange={registry.max_tokens_range}
                  onChange={setAgent(key)}
                  onReset={() => void askReset(key, spec.title)}
                  resetting={reset.isPending}
                >
                  {key === ATTENTION_AGENT && (
                    <AttentionBlock value={draft.attention} onChange={(attention) => setDraft((d) => ({ ...d, attention }))} />
                  )}
                  {key === GUARD && (
                    <Field id="agent-guard-rules" label="Запреты" hint="Что Охранник не должен пропускать. Одно правило на строку.">
                      <Textarea
                        id="agent-guard-rules"
                        className="max-h-96 min-h-24"
                        value={draft.guard_rules}
                        onChange={(e) => setDraft((d) => ({ ...d, guard_rules: e.target.value }))}
                      />
                    </Field>
                  )}
                </AgentCard>
              );
            })}
          </div>
        </section>
      ))}
      {errors.length > 0 && <InlineError>{errors.join('; ')}</InlineError>}
      <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-3">
        <span className="mr-auto text-sm text-muted-foreground" aria-live="polite">
          {dirty ? 'Агенты изменены, не сохранены' : 'Агенты сохранены'}
        </span>
        <Button variant="ghost" disabled={!dirty || save.isPending} onClick={() => { setDraft(baseline); setErrors([]); }}>
          <RotateCcw />
          Отменить
        </Button>
        <Button
          disabled={!dirty || save.isPending}
          onClick={() => {
            const found = draftErrors(draft, registry);
            setErrors(found);
            if (!found.length) save.mutate(draft);
          }}
        >
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Сохранить агентов
        </Button>
      </div>
    </div>
  );
}

/** «Агенты»: every AI function of the persona (docs/agents.md), grouped and tunable. */
export function AgentsSection() {
  const personaId = usePersonaId();
  const registry = useQuery({ queryKey: qk.agentRegistry, queryFn: agentApi.registry, staleTime: Infinity });
  const agents = useQuery({ queryKey: qk.agents(personaId), queryFn: () => agentApi.get(personaId) });
  const models = useQuery({ queryKey: qk.models, queryFn: personaApi.models, staleTime: 5 * 60_000 });
  const failed = registry.isError ? registry : agents.isError ? agents : null;
  return (
    <Section
      id="agents"
      title="Агенты"
      description="Каждая функция ИИ внутри персонажа: модель (из каталога Broker, по тому, что модель умеет), ключи, промт и лимит ответа."
    >
      {models.isError && <InlineError>Каталог моделей недоступен: {errorMessage(models.error)}</InlineError>}
      {failed && <ErrorState onRetry={() => void failed.refetch()} />}
      {!failed && (registry.data && agents.data ? (
        <AgentsEditor registry={registry.data} saved={agents.data} models={models.data?.items ?? []} />
      ) : (
        <Skeleton className="h-48 w-full" />
      ))}
    </Section>
  );
}
