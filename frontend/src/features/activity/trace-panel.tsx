import type * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ErrorState } from '@/components/empty-state';
import { costLabel, metric } from '@/components/message-parts';
import { Skeleton } from '@/components/ui/skeleton';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useIsPhone } from '@/hooks/use-media-query';
import { traceApi } from '@/lib/api/activity';
import { formatStamp } from '@/lib/format';
import { qk } from '@/lib/query';
import type { TraceDetail, TraceTimelineStep } from '@/lib/trace-models';
import { cn } from '@/lib/utils';
import { detailPairs, formatMs, timelineBar } from './log-logic';
import { KindBadge, StatusBadge } from './system-log';

const BAR_COLOR: Record<TraceTimelineStep['status'], string> = {
  ok: 'bg-success/70',
  error: 'bg-destructive/80',
  retry: 'bg-warning/80',
  skipped: 'bg-muted-foreground/40',
  warning: 'bg-warning/60',
};

/** Side panel: one request end to end — a timeline of its steps with durations, attempts and errors. */
export function TracePanel({ traceId, onClose }: { traceId: string | null; onClose: () => void }) {
  const isPhone = useIsPhone();
  const query = useQuery({
    queryKey: qk.trace(traceId ?? ''),
    queryFn: () => traceApi.trace(traceId ?? ''),
    enabled: traceId !== null,
  });
  return (
    <Sheet open={traceId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side={isPhone ? 'bottom' : 'right'} className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Путь запроса</SheetTitle>
          <SheetDescription className="font-mono text-xs break-all">{traceId}</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4 pb-6">
          {query.isPending ? (
            <div role="status" aria-label="Загружаем трейс…" className="flex flex-col gap-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : query.isError ? (
            <ErrorState title="Не удалось загрузить трейс" onRetry={() => void query.refetch()} />
          ) : (
            <TraceBody trace={query.data} />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function TraceBody({ trace }: { trace: TraceDetail }) {
  const attempts = trace.steps.filter((s) => s.kind === 'job').length;
  return (
    <>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-xl border p-3 text-sm sm:grid-cols-4">
        <Summary label="Итог">
          <StatusBadge status={trace.status} />
        </Summary>
        <Summary label="Всего">{formatMs(trace.duration_ms)}</Summary>
        <Summary label="Шагов / попыток задач">
          {trace.steps.length} / {attempts}
        </Summary>
        <Summary label="Стоимость ИИ">{costLabel(trace.cost_usd)}</Summary>
        <Summary label="Начало" wide>
          {formatStamp(trace.started_at)}
        </Summary>
      </dl>
      <ol aria-label="Шаги запроса" className="flex flex-col gap-2">
        {trace.steps.map((step) => (
          <StepItem key={step.id} step={step} total={trace.duration_ms} />
        ))}
      </ol>
    </>
  );
}

function Summary({ label, children, wide = false }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={cn('flex flex-col gap-0.5', wide && 'col-span-2')}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="tabular">{children}</dd>
    </div>
  );
}

function StepItem({ step, total }: { step: TraceTimelineStep; total: number }) {
  const bar = timelineBar(step.offset_ms, step.duration_ms, total);
  const pairs = detailPairs(step.detail);
  return (
    <li className={cn('flex flex-col gap-2 rounded-lg border p-3 text-sm', step.status === 'error' && 'border-destructive/40')}>
      <div className="flex flex-wrap items-center gap-2">
        <KindBadge kind={step.kind} />
        <span className="min-w-0 flex-1 font-medium [overflow-wrap:anywhere]">{step.name}</span>
        <StatusBadge status={step.status} />
      </div>
      <div className="relative h-1.5 w-full rounded-full bg-muted" aria-hidden>
        <div
          className={cn('absolute h-full rounded-full', BAR_COLOR[step.status])}
          style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
        />
      </div>
      <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground tabular">
        <span>+{formatMs(step.offset_ms)}</span>
        <span>Длительность: {formatMs(step.duration_ms)}</span>
        {step.attempt !== null && <span>Попытка {step.attempt}</span>}
        {step.http_status !== null && <span>HTTP {step.http_status}</span>}
        {step.actor && <span>{step.actor}</span>}
        {!step.in_trace && <span>шаг общей задачи (запущена другим запросом)</span>}
      </p>
      {step.error && (
        <pre className="rounded-md bg-destructive/8 p-2 text-xs whitespace-pre-wrap text-destructive [overflow-wrap:anywhere]">
          {step.error}
        </pre>
      )}
      {step.llm && (
        <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs tabular">
          <span>{step.llm.model || '—'}</span>
          <span>Вход: {metric(step.llm.tokens_in)}</span>
          <span>Выход: {metric(step.llm.tokens_out)}</span>
          <span>Из кеша: {metric(step.llm.cached_tokens)}</span>
          <span>{costLabel(step.llm.cost_usd)}</span>
        </p>
      )}
      {pairs.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {pairs.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="[overflow-wrap:anywhere]">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {(step.client || step.dialog_id !== null) && (
        <div className="flex flex-wrap gap-3 text-xs">
          {step.client && (
            <Link to="/clients/$id" params={{ id: String(step.client.id) }} className="text-primary hover:underline">
              {step.client.display_name || `Клиент № ${step.client.id}`}
            </Link>
          )}
          {step.dialog_id !== null && (
            <Link to="/inbox" search={{ d: step.dialog_id }} className="text-primary hover:underline">
              Открыть диалог
            </Link>
          )}
        </div>
      )}
    </li>
  );
}
