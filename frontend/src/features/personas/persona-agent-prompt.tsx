import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { ErrorState } from '@/components/empty-state';
import { Field } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { personasApi } from '@/lib/api/personas';
import type { PromptMode } from '@/lib/models';
import { qk } from '@/lib/query';
import { personaFieldAnchor } from './persona-prompt-logic';

const MODE_TEXT: Record<PromptMode, string> = {
  legacy:
    'Сейчас работает совместимый режим: промпт собирается из «Промпта персонажа», «Запретов», распорядка и стратегии. ' +
    'Ниже те же инструкции одним текстом. Пока вы не сохраните его здесь, для персонажа ничего не меняется.',
  unified:
    'Сейчас работает единый промпт: модель получает этот текст как есть. «Промпт персонажа», «Запреты» и тексты ' +
    'стратегии в него не подмешиваются, их правка на ответы не влияет.',
};

/**
 * The persona's single authoritative prompt. It has its own «Сохранить»: the text is saved apart from the
 * persona form, and only the owner's save here switches the persona from the composed prompt to this text.
 * What the model gets around it (link rule, answer format, context, fixed rules) is in «Итоговый промпт».
 */
export function PersonaAgentPrompt({ personaId }: { personaId: number }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<string | null>(null);
  const query = useQuery({ queryKey: qk.personaPrompt(personaId), queryFn: () => personasApi.prompt(personaId) });
  const save = useMutation({
    mutationFn: (text: string) => personasApi.savePrompt(personaId, text),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.personaPrompt(personaId) });
      setDraft(null);
      toast.success('Единый промпт сохранён');
    },
  });

  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError) return <ErrorState onRetry={() => void query.refetch()} />;
  const { mode, agent_prompt: saved } = query.data;
  const text = draft ?? saved;
  const unchanged = mode === 'unified' && text === saved;

  const submit = async (): Promise<void> => {
    const ok =
      mode === 'unified' ||
      (await confirm({
        title: 'Включить единый промпт?',
        message:
          'Персонаж начнёт отвечать по этому тексту во всех каналах. «Промпт персонажа», «Запреты» и тексты стратегии перестанут попадать в его промпт.',
        confirmLabel: 'Включить',
      }));
    if (ok) save.mutate(text);
  };

  return (
    <div className="flex flex-col gap-3 text-sm">
      <p role="status" className="text-xs text-muted-foreground">{MODE_TEXT[mode]}</p>
      <Field
        anchorId={personaFieldAnchor('agent_prompt')}
        label="Текст единого промпта"
        hint="Несохранённый текст модели не отправляется. Кнопка сохраняет только его; остальные поля персонажа сохраняются своей кнопкой."
      >
        <Textarea
          value={text}
          onChange={(e) => setDraft(e.target.value)}
          disabled={save.isPending}
          className="min-h-96 max-h-[75vh] font-mono text-[13px] md:text-[13px]"
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-xs text-muted-foreground">
          Распорядок недели по-прежнему даёт «где ты сейчас» в контексте; ссылки, формат ответа, охранник, напоминания и
          правка опечаток работают как раньше, они показаны в «Итоговом промпте».
        </p>
        <Button size="sm" variant="outline" disabled={save.isPending || unchanged || !text.trim()} onClick={() => void submit()}>
          Сохранить единый промпт
        </Button>
      </div>
    </div>
  );
}
