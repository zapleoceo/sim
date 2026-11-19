import type * as React from 'react';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ErrorState } from '@/components/empty-state';
import { costLabel, metric } from '@/components/message-parts';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useIsPhone } from '@/hooks/use-media-query';
import { traceApi } from '@/lib/api/activity';
import { formatStamp } from '@/lib/format';
import { qk } from '@/lib/query';
import type { TraceDetail, TracePayload, TraceRequestBody, TraceTimelineStep } from '@/lib/trace-models';
import { cn } from '@/lib/utils';
import { agentLabel, detailPairs, formatMs, payloadText, showsRequestBody, timelineBar } from './log-logic';
import { KindBadge, StatusBadge } from './system-log';

const BAR_COLOR: Record<TraceTimelineStep['status'], string> = {
  ok: 'bg-success/70',
  error: 'bg-destructive/80',
  retry: 'bg-warning/80',
  skipped: 'bg-muted-foreground/40',
  warning: 'bg-warning/60',
};

/** Side panel: one request end to end — a timeline of its steps with durations, attempts and errors. */
export function TracePanel({ traceId, selectedStepId, selectedCallId, onClose }: {
  traceId: string | null; selectedStepId?: number; selectedCallId?: number; onClose: () => void;
}) {
  const isPhone = useIsPhone();
  const query = useQuery({
    queryKey: qk.trace(traceId ?? ''),
    queryFn: () => traceApi.trace(traceId ?? ''),
    enabled: traceId !== null,
  });
  return (
    <Sheet open={traceId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className={cn('w-full overflow-y-auto sm:max-w-2xl', isPhone && 'max-w-none')}>
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
            <TraceBody key={`${traceId}:${selectedStepId ?? ''}:${selectedCallId ?? ''}`} trace={query.data}
              selectedStepId={selectedStepId} selectedCallId={selectedCallId} />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function TraceBody({ trace, selectedStepId, selectedCallId }: {
  trace: TraceDetail; selectedStepId?: number; selectedCallId?: number;
}) {
  const attempts = trace.steps.filter((s) => s.kind === 'job').length;
  const selected = trace.steps.find(s => selectedStepId !== undefined
    ? s.id === selectedStepId : selectedCallId !== undefined && s.llm_call_id === selectedCallId);
  const steps = selected ? [selected, ...trace.steps.filter(s => s.id !== selected.id)] : trace.steps;
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
      {(selectedStepId || selectedCallId) && !selected && <p role="status">Выбранная запись не найдена в этом запросе. Ниже — доступные шаги.</p>}
      {selected && <p className="text-sm text-muted-foreground">Сначала выбранная запись, затем остальные шаги по времени.</p>}
      <ol aria-label="Шаги запроса" className="flex flex-col gap-2">
        {steps.map((step) => (
          <StepItem key={step.id} step={step} total={trace.duration_ms} selected={step.id === selected?.id} />
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

function StepItem({ step, total, selected = false }: { step: TraceTimelineStep; total: number; selected?: boolean }) {
  const bar = timelineBar(step.offset_ms, step.duration_ms, total);
  const pairs = detailPairs(step.detail);
  return (
    <li aria-label={selected ? 'Выбранная запись' : undefined}
      className={cn('flex flex-col gap-2 rounded-lg border p-3 text-sm', selected && 'border-primary', step.status === 'error' && 'border-destructive/40')}>
      <div className="flex flex-wrap items-center gap-2">
        <KindBadge kind={step.kind} />
        <span className="min-w-0 flex-1 font-medium [overflow-wrap:anywhere]">{step.name}</span>
        <StatusBadge status={step.status} step={step} />
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
        {step.actor && <span>Исполнитель: {step.actor}</span>}
        {!step.in_trace && <span>шаг общей задачи (запущена другим запросом)</span>}
      </p>
      {step.error && (
        <pre className="rounded-md bg-destructive/8 p-2 text-xs whitespace-pre-wrap text-destructive [overflow-wrap:anywhere]">
          {step.error}
        </pre>
      )}
      {(step.kind === 'llm' || step.kind === 'guard') && <dl aria-label="Агент и модель"
        className="grid min-w-0 grid-cols-1 gap-2 rounded bg-muted/50 p-3 sm:grid-cols-2 [overflow-wrap:anywhere]">
        <Summary label="Агент / роль">{agentLabel(step.llm?.workflow)}</Summary>
        <Summary label="Сохранённая модель">{step.llm?.model || 'Не сохранена'}</Summary>
        <Summary label="Провайдер">{step.llm?.provider || 'Не сохранён'}</Summary>
        <Summary label="Роль в журнале">{step.llm?.workflow || 'Не сохранена'}</Summary>
        <div className="text-xs text-muted-foreground sm:col-span-2">
          В старых записях может быть указана запрошенная модель, а не фактически ответившая.
        </div>
      </dl>}
      {step.llm && (
        <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs tabular">
          <span>Вход: {metric(step.llm.tokens_in)}</span>
          <span>Выход: {metric(step.llm.tokens_out)}</span>
          <span>Из кеша: {metric(step.llm.cached_tokens)}</span>
          <span>{costLabel(step.llm.cost_usd)}</span>
        </p>
      )}
      {(step.kind === 'llm' || step.kind === 'guard') && <BrokerExchange key={step.id} exchange={step.broker_exchange} initiallyOpen={selected} />}
      {showsRequestBody(step) && <RequestBody key={step.id} body={step.request_body} />}
      {pairs.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {pairs.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="whitespace-pre-line [overflow-wrap:anywhere]">{v}</dd>
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

/** One stored payload (broker request/response, request body): copy button, truncation note, missing state. */
function PayloadDetails({ title, payload, missing, copyLabel, copiedText, onStatus, initiallyOpen = false }: {
  title: string;
  payload: TracePayload | null | undefined;
  missing: React.ReactNode;
  copyLabel: string;
  copiedText: string;
  onStatus: (status: string) => void;
  initiallyOpen?: boolean;
}) {
  const value = payload ? payloadText(payload.content) : '';
  return <details open={initiallyOpen} className="rounded border p-2">
    <summary className="cursor-pointer font-medium">{title}</summary>
    {!payload ? <p>{missing}</p> : <>
      {payload.truncated && <p role="note">Данные усечены по лимиту хранения.</p>}
      <Button size="sm" variant="outline" onClick={() => {
        if (!navigator.clipboard) { onStatus('Копирование недоступно в этом браузере'); return; }
        void navigator.clipboard.writeText(value).then(() => onStatus(copiedText), () => onStatus('Не удалось скопировать'));
      }}>{copyLabel}</Button>
      <pre className="max-h-[50dvh] overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-xs [overflow-wrap:anywhere]">{value}</pre>
    </>}
  </details>;
}

export function BrokerExchange({ exchange, initiallyOpen = false }: {
  exchange: TraceTimelineStep['broker_exchange']; initiallyOpen?: boolean;
}) {
  const [copyStatus, setCopyStatus] = useState('');
  return <section aria-label="Запрос и ответ брокера" className="space-y-3">
    <p className="text-xs text-muted-foreground">Сохранённые данные вызова. Секреты скрыты на сервере; промежуточные опросы не сохраняются.</p>
    {(['request', 'response'] as const).map(key => {
      const label = key === 'request' ? 'Запрос' : 'Ответ';
      return <PayloadDetails key={key} title={`${label} брокера`} payload={exchange?.[key]} initiallyOpen={initiallyOpen}
        missing={`Полный ${label.toLowerCase()} не сохранён`} copyLabel={`Копировать ${label.toLowerCase()}`}
        copiedText={`${label} скопирован`} onStatus={setCopyStatus} />;
    })}
    {copyStatus && <p role="status">{copyStatus}</p>}
  </section>;
}

export function RequestBody({ body }: { body: TraceRequestBody | null | undefined }) {
  const [copyStatus, setCopyStatus] = useState('');
  const omitted = body && 'omitted' in body ? body.omitted : null;
  return <section aria-label="Тело запроса" className="space-y-3">
    <PayloadDetails title="Тело запроса" payload={body && !('omitted' in body) ? body : null}
      missing={omitted ? `Тело не сохранено: ${omitted}` : 'Тело не сохранено'}
      copyLabel="Копировать тело" copiedText="Тело скопировано" onStatus={setCopyStatus} />
    {copyStatus && <p role="status">{copyStatus}</p>}
  </section>;
}
