import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ErrorState } from '@/components/empty-state';
import { Badge } from '@/components/ui/badge';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { dayTitle } from '@/features/persona/life/life-logic';
import { formatDateTime, formatInt, formatMs, formatUsd } from '@/lib/format';
import { qk } from '@/lib/query';
import type { LlmCall, ReplyRun } from '@/lib/types';
import { prettyJson } from '@/lib/utils';
import { chatsApi } from './api';

/**
 * Context blocks in the order the model reads them, stable first (docs/context.md §2); unknown
 * keys follow. `days`/`weeks` are also shown as a list («Дни в контексте»).
 */
const CONTEXT_LABELS: [string, string][] = [
  ['routine', 'Распорядок'],
  ['arcs', 'Истории'],
  ['canon', 'Канон'],
  ['client', 'Карточка клиента'],
  ['client_card', 'Карточка клиента'],
  ['summary', 'Давняя переписка'],
  ['weeks', 'Итоги недель'],
  ['days', 'Итоги дней'],
  ['morning', 'Начало дня, кратко'],
  ['timeline', 'Лента за сегодня'],
  ['notes', 'Заметки дня'],
  ['facts', 'Факты от кода'],
  ['now', 'Точные данные на сейчас'],
  ['catalog', 'Каталог медиа'],
  ['media', 'Каталог медиа'],
  ['media_catalog', 'Каталог медиа'],
  ['recall_text', 'Вспомнила'],
];

export interface ContextDay {
  day: string;
  summary: string;
}

/** The day summaries that were in the model's context (`context.days`), oldest first. */
export function contextDays(context: Record<string, unknown> | null): ContextDay[] {
  const days = context?.days;
  if (!Array.isArray(days)) return [];
  return days.flatMap((d: unknown) => {
    if (!d || typeof d !== 'object') return [];
    const { day, summary } = d as Record<string, unknown>;
    return typeof day === 'string' && typeof summary === 'string' ? [{ day, summary }] : [];
  });
}

function DaysInContext({ context }: { context: Record<string, unknown> | null }) {
  const days = contextDays(context);
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">Дни в контексте</h3>
      {days.length === 0 ? (
        <p className="text-sm text-muted-foreground">Итогов прошлых дней в контексте не было.</p>
      ) : (
        <ol className="flex flex-col gap-1.5">
          {days.map((d) => (
            <li key={d.day} className="text-sm">
              <span className="font-medium first-letter:uppercase">{dayTitle(d.day)}:</span> {d.summary}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function contextBlocks(context: Record<string, unknown> | null): { key: string; title: string; text: string }[] {
  if (!context) return [];
  const known = new Map(CONTEXT_LABELS);
  const order = [...CONTEXT_LABELS.map(([k]) => k), ...Object.keys(context).filter((k) => !known.has(k))];
  return order
    .filter((key) => key in context && context[key] !== null && context[key] !== '')
    .map((key) => ({ key, title: known.get(key) ?? key, text: prettyJson(context[key]) }));
}

const RUN_STATUS: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'muted' }> = {
  running: { label: 'в работе', variant: 'muted' },
  sent: { label: 'отправлен', variant: 'success' },
  blocked: { label: 'заблокирован', variant: 'warning' },
  skipped: { label: 'пропущен', variant: 'muted' },
  failed: { label: 'ошибка', variant: 'destructive' },
};

function Pre({ children }: { children: string }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-md bg-muted/60 p-2.5 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
      {children}
    </pre>
  );
}

function Block({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  return (
    <details open={open} className="rounded-lg border px-3 py-2">
      <summary className="cursor-pointer text-sm font-medium select-none">{title}</summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

function verdictText(v: unknown): { ok: boolean | null; reason: string } {
  if (!v || typeof v !== 'object') return { ok: null, reason: prettyJson(v) };
  const o = v as Record<string, unknown>;
  const verdict = o.verdict ?? o.result ?? o.ok;
  const ok = verdict === 'ok' || verdict === true ? true : verdict === 'block' || verdict === false ? false : null;
  const reason = typeof o.reason === 'string' ? o.reason : '';
  return { ok, reason };
}

function LlmCallView({ call }: { call: LlmCall }) {
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <Badge variant="secondary">{call.purpose}</Badge>
        <span className="min-w-0 font-mono text-xs break-all">{call.model}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:grid-cols-4">
        <div><dt className="text-muted-foreground">Токены вход</dt><dd className="tabular">{formatInt(call.tokens_in)}</dd></div>
        <div><dt className="text-muted-foreground">Токены выход</dt><dd className="tabular">{formatInt(call.tokens_out)}</dd></div>
        <div><dt className="text-muted-foreground">Стоимость</dt><dd className="tabular">{formatUsd(call.cost_usd)}</dd></div>
        <div><dt className="text-muted-foreground">Время</dt><dd className="tabular">{formatMs(call.latency_ms)}</dd></div>
      </dl>
      {call.error && <p className="text-xs text-destructive">{call.error}</p>}
      <details>
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground select-none">Запрос</summary>
        <div className="mt-1.5"><Pre>{prettyJson(call.request)}</Pre></div>
      </details>
      <details>
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground select-none">Ответ</summary>
        <div className="mt-1.5"><Pre>{prettyJson(call.response)}</Pre></div>
      </details>
    </li>
  );
}

export function ReplyRunView({ run }: { run: ReplyRun }) {
  const status = RUN_STATUS[run.status] ?? { label: run.status, variant: 'muted' as const };
  const blocks = contextBlocks(run.context);
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">Ход №{run.id}</span>
        <Badge variant={status.variant}>{status.label}</Badge>
      </div>
      {run.error && <p role="alert" className="text-sm text-destructive">{run.error}</p>}

      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Что знала модель</h3>
        {blocks.length === 0 && <p className="text-sm text-muted-foreground">Контекст не сохранён.</p>}
        {blocks.map((b) => (
          <Block key={b.key} title={b.title} open={b.key === 'facts'}>
            <Pre>{b.text}</Pre>
          </Block>
        ))}
      </section>

      {run.context && <DaysInContext context={run.context} />}

      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">План ответа</h3>
        <Pre>{prettyJson(run.plan ?? {})}</Pre>
      </section>

      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Проверка</h3>
        {run.guard_verdicts.length === 0 && <p className="text-sm text-muted-foreground">Проверка не запускалась.</p>}
        <ol className="flex flex-col gap-1.5">
          {run.guard_verdicts.map((v, i) => {
            const { ok, reason } = verdictText(v);
            return (
              <li key={i} className="flex flex-wrap items-baseline gap-2 text-sm">
                <span className="text-muted-foreground">Попытка {i + 1}:</span>
                <Badge variant={ok === true ? 'success' : ok === false ? 'warning' : 'muted'}>
                  {ok === true ? 'пропущен' : ok === false ? 'заблокирован' : 'неизвестно'}
                </Badge>
                {reason && <span className="min-w-0 break-words">{reason}</span>}
              </li>
            );
          })}
        </ol>
      </section>

      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Вызовы моделей ({run.llm_calls.length})</h3>
        <ol className="flex flex-col gap-2">
          {run.llm_calls.map((c) => <LlmCallView key={c.id} call={c} />)}
        </ol>
        {run.llm_calls.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Итого: {formatUsd(run.llm_calls.reduce((s, c) => s + (c.cost_usd ?? 0), 0))}, последний вызов{' '}
            {formatDateTime(run.llm_calls[run.llm_calls.length - 1]?.created_at)}
          </p>
        )}
      </section>
    </div>
  );
}

/** «Почему так»: what went into the model for one persona turn, what came back, what the guard said. */
export function ReplyRunSheet({ runId, onClose }: { runId: number | null; onClose: () => void }) {
  const run = useQuery({
    queryKey: qk.replyRun(runId ?? 0),
    queryFn: () => chatsApi.replyRun(runId as number),
    enabled: runId !== null,
    staleTime: 60_000,
  });
  return (
    <Sheet open={runId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full gap-0 sm:max-w-2xl">
        <SheetHeader className="border-b">
          <SheetTitle>Почему так ответила</SheetTitle>
          <SheetDescription>Контекст, план, проверка и вызовы моделей этого хода.</SheetDescription>
        </SheetHeader>
        <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">
          {run.isPending && (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-6 w-40" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          )}
          {run.isError && <ErrorState onRetry={() => void run.refetch()} />}
          {run.data && <ReplyRunView run={run.data} />}
        </div>
      </SheetContent>
    </Sheet>
  );
}
