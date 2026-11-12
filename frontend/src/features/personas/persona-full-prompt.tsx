import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { ErrorState } from '@/components/empty-state';
import { FixedPrompt } from '@/components/fixed-prompt';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { personasApi } from '@/lib/api/personas';
import { qk } from '@/lib/query';

/**
 * Everything the persona's model gets as its system prompt, for the saved persona: the persona
 * prompt, the fixed honesty rule, the strategy and the answer format. Loaded when opened.
 */
export function PersonaFullPrompt({ personaId }: { personaId: number }) {
  const [open, setOpen] = useState(false);
  const query = useQuery({ queryKey: qk.personaPrompt(personaId), queryFn: () => personasApi.prompt(personaId), enabled: open });

  return (
    <div className="flex flex-col gap-2 text-sm">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="self-start px-0 text-muted-foreground"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown /> : <ChevronRight />}
        Полный промпт, который получает модель
      </Button>
      {open && (
        <div className="flex flex-col gap-2">
          {query.isPending && <Skeleton className="h-40 w-full" />}
          {query.isError && <ErrorState onRetry={() => void query.refetch()} />}
          {query.data && (
            <>
              <FixedPrompt text={query.data.fixed} where="после промпта персонажа" />
              <pre
                aria-label="Полный промпт"
                className="max-h-[60vh] overflow-auto rounded-lg bg-muted/60 p-3 text-[12px] whitespace-pre-wrap [overflow-wrap:anywhere]"
              >
                {query.data.system}
              </pre>
              <p className="text-xs text-muted-foreground">
                По сохранённым настройкам. После него модель получает историю диалога и контекст разговора (этап, ссылки, время).
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
