import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { toast } from 'sonner';
import { Field } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
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

  return (
    <div className="flex flex-col gap-2">
      <Field
        anchorId={STRATEGY_PROMPT_ANCHOR}
        label={`Сценарий стратегии «${strategy.name}»`}
        hint="Один текст: цель, шаги воронки, тон. Общий для всех персонажей с этой стратегией."
      >
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="min-h-48 max-h-[70vh] font-mono text-[13px] md:text-[13px]"
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Link
          to="/strategies/$id"
          params={{ id: String(strategy.id) }}
          className="text-xs font-medium text-primary underline-offset-4 hover:underline"
        >
          Оффер, ссылка регистрации и напоминания: в карточке стратегии
        </Link>
        <span className="flex-1" />
        <Button size="sm" variant="outline" disabled={save.isPending || draft === strategy.prompt} onClick={() => save.mutate()}>
          Сохранить сценарий
        </Button>
      </div>
    </div>
  );
}
