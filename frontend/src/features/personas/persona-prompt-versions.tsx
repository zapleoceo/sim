import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { personasApi } from '@/lib/api/personas';
import { formatDateTime } from '@/lib/format';
import type { Persona } from '@/lib/models';
import { qk } from '@/lib/query';

/**
 * Every saved text of the persona's prompt, newest (the one in use) first. «Восстановить» puts an
 * older text back in use as a new version, so the history only grows.
 */
export function PersonaPromptVersions({ personaId, onRestored }: {
  personaId: number;
  /** The restored text, now in use: the persona form shows it in its prompt field. */
  onRestored?: (text: string) => void;
}) {
  const queryClient = useQueryClient();
  const versions = useQuery({ queryKey: qk.personaPromptVersions(personaId), queryFn: () => personasApi.promptVersions(personaId) });
  const restore = useMutation({
    mutationFn: (versionId: number) => personasApi.restorePrompt(personaId, versionId),
    onSuccess: (body) => {
      queryClient.setQueryData(qk.personaPromptVersions(personaId), body.items);
      queryClient.setQueryData<Persona[]>(qk.personas, (list) =>
        list?.map((p) => (p.id === personaId ? { ...p, agent_prompt: body.agent_prompt } : p)));
      onRestored?.(body.agent_prompt);
      toast.success('Версия восстановлена');
    },
  });

  // The history is secondary to the editor: while it loads or fails, the editor stands alone.
  if (versions.isPending || versions.isError) return null;
  const items = versions.data;
  if (items.length === 0) return null;

  return (
    <details className="rounded-lg border p-3 text-sm">
      <summary className="cursor-pointer font-medium">Версии промпта ({items.length})</summary>
      <ol aria-label="Версии промпта" className="mt-3 flex flex-col gap-3">
        {items.map((v, i) => (
          <li key={v.id} aria-label={`Версия от ${formatDateTime(v.created_at)}`} className="flex flex-col gap-1 border-t pt-3 first:border-t-0 first:pt-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium tabular">{formatDateTime(v.created_at)}</span>
              {i === 0 && <Badge variant="muted">текущая</Badge>}
              <span className="text-xs text-muted-foreground">{v.author || 'автор не записан'} · {v.text.length} знаков</span>
            </div>
            {v.note && <p className="text-xs text-muted-foreground">{v.note}</p>}
            <details>
              <summary className="cursor-pointer text-xs">Показать текст</summary>
              <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 font-mono text-[12px]">{v.text}</pre>
            </details>
            {i > 0 && (
              <Button className="self-start" size="sm" variant="outline" disabled={restore.isPending} onClick={() => restore.mutate(v.id)}>
                Восстановить
              </Button>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}
