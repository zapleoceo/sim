import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { ErrorState } from '@/components/empty-state';
import { Field } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { personasApi } from '@/lib/api/personas';
import { qk } from '@/lib/query';
import { personaFieldAnchor } from './persona-prompt-logic';

/**
 * The persona's single authoritative prompt. It has its own «Сохранить»: the text is saved apart from the
 * persona form. What the model gets around it (link rule, answer format, context, fixed rules) is in
 * «Итоговый промпт».
 */
export function PersonaAgentPrompt({ personaId }: { personaId: number }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const query = useQuery({ queryKey: qk.personaPrompt(personaId), queryFn: () => personasApi.prompt(personaId) });
  const save = useMutation({
    mutationFn: (text: string) => personasApi.savePrompt(personaId, text),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.personaPrompt(personaId) });
      await queryClient.invalidateQueries({ queryKey: ['persona-context', personaId] });
      setDraft(null);
      toast.success('Промпт сохранён');
    },
  });

  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError) return <ErrorState onRetry={() => void query.refetch()} />;
  const saved = query.data.agent_prompt ?? '';
  const text = draft ?? saved;
  const unchanged = text === saved;

  return (
    <div className="flex flex-col gap-3 text-sm">
      <p role="status" className="text-xs text-muted-foreground">
        Модель получает этот текст как есть. Ссылки, формат ответа, проверка и контекст добавляются кодом
        и показаны в «Итоговом промпте».
      </p>
      <Field
        anchorId={personaFieldAnchor('agent_prompt')}
        label="Промпт агента"
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
          Правила честности и безопасности сохраняются. Распорядок даёт «где ты сейчас» в контексте; ссылки, формат ответа, проверка, напоминания и
          правка опечаток работают как раньше, они показаны в «Итоговом промпте».
        </p>
        <Button size="sm" variant="outline" disabled={save.isPending || unchanged || !text.trim()} onClick={() => void submit()}>
          Сохранить промпт
        </Button>
      </div>
    </div>
  );

  function submit(): Promise<void> {
    save.mutate(text);
    return Promise.resolve();
  }
}
