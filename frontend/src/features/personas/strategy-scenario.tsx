import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { toast } from 'sonner';
import { LongText } from '@/components/long-text';
import { Button } from '@/components/ui/button';
import { strategiesApi } from '@/lib/api/strategies';
import type { Strategy } from '@/lib/models';
import { qk } from '@/lib/query';
import { upsertById } from '@/lib/utils';
import { STRATEGY_PROMPT_ANCHOR } from './persona-prompt-logic';

/**
 * The scenario text of the persona's strategy, edited right on the persona page. It has its own
 * «Сохранить»: the text belongs to the strategy, so it changes for every persona that uses it.
 * The rest of the strategy (offer, registration link, reminders) is edited on its own page.
 */
export function StrategyScenario({ strategy }: { strategy: Strategy }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(strategy.prompt);
  const save = useMutation({
    mutationFn: () => strategiesApi.savePrompt(strategy, draft),
    onSuccess: (saved) => {
      queryClient.setQueryData<Strategy[]>(qk.strategies, (list) => upsertById(list ?? [], saved));
      void queryClient.invalidateQueries({ queryKey: qk.personaPrompts });
      setDraft(saved.prompt);
      toast.success('Сценарий стратегии сохранён');
    },
  });
  const dirty = draft !== strategy.prompt;

  return (
    <div className="flex flex-col gap-2">
      <LongText
        anchorId={STRATEGY_PROMPT_ANCHOR}
        label={`Сценарий стратегии «${strategy.name}»`}
        hint="Один текст: цель, шаги воронки, тон. Общий для всех персонажей с этой стратегией."
        value={draft}
        onChange={setDraft}
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Link
          to="/strategies/$id"
          params={{ id: String(strategy.id) }}
          className="text-xs font-medium text-primary underline-offset-4 hover:underline"
        >
          Оффер, ссылка регистрации и напоминания: в карточке стратегии
        </Link>
        <span className="flex-1" />
        {dirty && <span className="text-xs text-muted-foreground">есть несохранённые правки</span>}
        <Button size="sm" variant="outline" disabled={save.isPending || !dirty} onClick={() => save.mutate()}>
          Сохранить сценарий
        </Button>
      </div>
    </div>
  );
}
