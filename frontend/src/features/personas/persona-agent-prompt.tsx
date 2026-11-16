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
 * The persona's single authoritative prompt — the main editable text the model gets as-is.
 * Has its own «Сохранить»: the text is saved apart from the persona form.
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
      <Field
        anchorId={personaFieldAnchor('agent_prompt')}
        label="Промпт"
        hint="Кто она, легенда, характер, стиль, язык, запреты — всё в одном тексте. Несохранённый текст модели не отправляется."
      >
        <Textarea
          value={text}
          onChange={(e) => setDraft(e.target.value)}
          disabled={save.isPending}
          className="min-h-[28rem] max-h-[75vh] font-mono text-[13px] md:text-[13px]"
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-xs text-muted-foreground">
          Распорядок, ссылки, формат ответа, проверка и контекст добавляются кодом — показаны в «Контексте модели».
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
