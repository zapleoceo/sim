import { useQuery } from '@tanstack/react-query';
import { ErrorState } from '@/components/empty-state';
import { Section } from '@/components/field';
import { Skeleton } from '@/components/ui/skeleton';
import { qk } from '@/lib/query';
import { personaApi } from './api';

/** The JSON-plan format rules: a constant in the backend code, shown read-only. */
export function FormatRulesSection() {
  const rules = useQuery({ queryKey: qk.instructions, queryFn: personaApi.instructions, staleTime: Infinity });
  return (
    <Section id="format" title="Правила формата" description="Как модель отвечает планом. Задаются в коде, здесь только для чтения.">
      {rules.isPending && <Skeleton className="h-40 w-full" />}
      {rules.isError && <ErrorState onRetry={() => void rules.refetch()} />}
      {rules.data && (
        <pre className="max-h-96 overflow-auto rounded-md bg-muted/60 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
          {rules.data.text}
        </pre>
      )}
    </Section>
  );
}
