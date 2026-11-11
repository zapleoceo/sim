import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { RefreshCw, SearchX, SlidersHorizontal, TriangleAlert, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { EmptyState, ErrorState, InlineError } from '@/components/empty-state';
import { SearchInput } from '@/components/filter-bar';
import { Pager } from '@/components/pager';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table';
import { useDebounced } from '@/hooks/use-debounced';
import { traceApi } from '@/lib/api/activity';
import type { PageRequest } from '@/lib/api/http';
import { formatStamp } from '@/lib/format';
import { qk } from '@/lib/query';
import type { TraceKind, TraceSortField, TraceStatus, TraceStep } from '@/lib/trace-models';
import { CheckList, ColumnHeader } from './column-header';
import {
  DEFAULT_SORT, EMPTY_LOG_FILTERS, KIND_LABELS, KINDS, STATUS_LABELS, STATUSES, activeFilterCount, activeFilters,
  formatMs, nextSort, shortTrace, toLogParams, toggle, validateLogFilters, type LogFilters, type LogSort,
} from './log-logic';

const LIVE_INTERVAL_MS = 5000;

const STATUS_VARIANT: Record<TraceStatus, 'success' | 'destructive' | 'warning' | 'muted'> = {
  ok: 'success',
  error: 'destructive',
  retry: 'warning',
  skipped: 'muted',
};

export function StatusBadge({ status }: { status: TraceStatus }) {
  return <Badge variant={STATUS_VARIANT[status]}>{STATUS_LABELS[status]}</Badge>;
}

export function KindBadge({ kind }: { kind: TraceKind }) {
  return <Badge variant="outline">{KIND_LABELS[kind]}</Badge>;
}

/**
 * The system log: every request and its processing steps. Sorting and filters live in the column headers
 * and run on the server; the bar above holds the search, «only errors» and the live mode.
 */
export function SystemLog({ channelId, onOpenTrace }: { channelId: number | null; onOpenTrace: (traceId: string) => void }) {
  const [filters, setFiltersState] = useState<LogFilters>({ ...EMPTY_LOG_FILTERS, channel_id: channelId });
  const [sort, setSort] = useState<LogSort>(DEFAULT_SORT);
  const [page, setPage] = useState({ page: 1, size: 50 });
  const [live, setLive] = useState(false);

  const setFilters = (next: LogFilters): void => {
    setFiltersState(next);
    setPage((p) => ({ ...p, page: 1 }));
  };
  const set = <K extends keyof LogFilters>(key: K, value: LogFilters[K]): void => setFilters({ ...filters, [key]: value });

  useEffect(() => {
    setFiltersState((f) => ({ ...f, channel_id: channelId }));
    setPage((p) => ({ ...p, page: 1 }));
  }, [channelId]);

  const settled = useDebounced(filters, 300);
  const params = useMemo(() => toLogParams(settled, sort, page), [settled, sort, page]);
  const errors = validateLogFilters(filters);
  const marks = activeFilters(filters);
  const count = activeFilterCount(filters);

  const query = useQuery({
    queryKey: qk.activity('log', params),
    queryFn: () => traceApi.log(params ?? {}),
    enabled: params !== null,
    placeholderData: keepPreviousData,
    refetchInterval: live ? LIVE_INTERVAL_MS : false,
  });

  const onSort = (field: TraceSortField): void => {
    setSort((s) => nextSort(s, field));
    setPage((p) => ({ ...p, page: 1 }));
  };
  const head = { sort, onSort };
  const text = (key: 'name' | 'actor' | 'client' | 'trace_id', placeholder: string) => (
    <Input autoFocus value={filters[key]} placeholder={placeholder} maxLength={200} onChange={(e) => set(key, e.target.value)} />
  );
  const number = (key: 'min_ms' | 'max_ms' | 'min_attempt' | 'http_status', label: string) => (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      {label}
      <Input
        inputMode="numeric"
        value={filters[key]}
        aria-invalid={errors[key] ? true : undefined}
        onChange={(e) => set(key, e.target.value)}
      />
      {errors[key] && <span className="text-destructive">{errors[key]}</span>}
    </label>
  );

  const pageRequest: PageRequest = { limit: page.size, offset: (page.page - 1) * page.size };
  const data = query.data;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={filters.q}
          maxLength={200}
          onChange={(q) => set('q', q)}
          placeholder="Поиск: шаг, ошибка, клиент, кто, трейс"
          className="min-w-56"
        />
        <Button
          variant={filters.errors_only ? 'secondary' : 'outline'}
          aria-pressed={filters.errors_only}
          onClick={() => set('errors_only', !filters.errors_only)}
        >
          <TriangleAlert />
          Только ошибки
        </Button>
        {/* Columns hidden on narrow screens keep their filters here. */}
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="lg:hidden">
              <SlidersHorizontal />
              Ещё фильтры
            </Button>
          </PopoverTrigger>
          <PopoverContent className="flex flex-col gap-2 text-sm" aria-label="Ещё фильтры">
            {number('min_attempt', 'Попытка, не меньше')}
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Кто
              <Input value={filters.actor} maxLength={120} onChange={(e) => set('actor', e.target.value)} />
            </label>
            {number('http_status', 'HTTP-код')}
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Трейс (начало id)
              <Input value={filters.trace_id} maxLength={32} onChange={(e) => set('trace_id', e.target.value)} />
            </label>
          </PopoverContent>
        </Popover>
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={live} onCheckedChange={setLive} aria-label="Живой режим" />
          Живой режим
        </label>
        {count > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setFilters({ ...EMPTY_LOG_FILTERS })}>
            <X />
            Сбросить фильтры ({count})
          </Button>
        )}
        <Button variant="outline" size="icon" aria-label="Обновить" onClick={() => void query.refetch()} disabled={query.isFetching || params === null} className="ml-auto">
          <RefreshCw className={query.isFetching ? 'animate-spin' : undefined} />
        </Button>
      </div>
      {marks.channel && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          Только канал № {filters.channel_id}
          <Button variant="ghost" size="sm" onClick={() => set('channel_id', null)}>
            Все каналы
          </Button>
        </p>
      )}
      {(errors.range || errors.dates || errors.q) && <InlineError>{errors.range ?? errors.dates ?? errors.q}</InlineError>}

      <div className="overflow-x-auto rounded-xl border" aria-busy={query.isFetching}>
        <Table className="text-sm">
          <TableHeader>
            <TableRow>
              <ColumnHeader field="started_at" label="Время" {...head} filtered={marks.started_at} filter={
                <>
                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    С
                    <Input type="datetime-local" value={filters.from} onChange={(e) => set('from', e.target.value)} />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    По
                    <Input type="datetime-local" value={filters.to} onChange={(e) => set('to', e.target.value)} />
                  </label>
                </>
              } />
              <ColumnHeader field="kind" label="Тип" {...head} filtered={marks.kind} filter={
                <CheckList
                  options={KINDS.map((k) => ({ value: k, label: KIND_LABELS[k] }))}
                  value={filters.kinds}
                  onToggle={(k) => set('kinds', toggle(filters.kinds, k))}
                />
              } />
              <ColumnHeader field="name" label="Шаг" {...head} filtered={marks.name} filter={text('name', 'Содержит, например reply')} />
              <ColumnHeader field="status" label="Статус" {...head} filtered={marks.status} filter={
                <CheckList
                  options={STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))}
                  value={filters.statuses}
                  onToggle={(s) => set('statuses', toggle(filters.statuses, s))}
                />
              } />
              <ColumnHeader field="duration_ms" label="Длительность" align="right" {...head} filtered={marks.duration_ms} filter={
                <div className="grid grid-cols-2 gap-2">
                  {number('min_ms', 'От, мс')}
                  {number('max_ms', 'До, мс')}
                </div>
              } />
              <ColumnHeader field="attempt" label="Попытка" align="right" className="hidden md:table-cell" {...head} filtered={marks.attempt} filter={number('min_attempt', 'Не меньше')} />
              <ColumnHeader field="client" label="Клиент" {...head} filtered={marks.client} filter={text('client', 'Имя клиента')} />
              <ColumnHeader field="actor" label="Кто" className="hidden lg:table-cell" {...head} filtered={marks.actor} filter={text('actor', 'Админ, worker, канал')} />
              <ColumnHeader field="http_status" label="HTTP" align="right" className="hidden lg:table-cell" {...head} filtered={marks.http_status} filter={number('http_status', 'Код ответа')} />
              <ColumnHeader field="trace_id" label="Трейс" className="hidden md:table-cell" {...head} filtered={marks.trace_id} filter={text('trace_id', 'Начало id трейса')} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {params === null ? null : query.isPending ? (
              Array.from({ length: 6 }, (_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={10}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : (
              data?.items.map((step) => <LogRow key={step.id} step={step} onOpen={() => onOpenTrace(step.trace_id)} />)
            )}
          </TableBody>
        </Table>
        {params === null && <InlineError className="m-3">Исправьте фильтры, чтобы загрузить журнал.</InlineError>}
        {query.isError && <ErrorState title="Не удалось загрузить журнал" onRetry={() => void query.refetch()} />}
        {data && data.items.length === 0 && (
          <EmptyState icon={SearchX} title="Записей не найдено" hint="Измените фильтры или включите живой режим и дождитесь событий." />
        )}
      </div>

      {data && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            Журнал хранится {data.retention_days} дн. Время — ваше локальное. Нажмите строку, чтобы увидеть весь путь запроса.
          </p>
          {data.total > 0 && (
            <Pager
              page={pageRequest}
              total={data.total}
              onChange={(p) => setPage({ page: Math.floor(p.offset / p.limit) + 1, size: p.limit })}
              label="Страницы журнала"
            />
          )}
        </div>
      )}
    </div>
  );
}

function LogRow({ step, onOpen }: { step: TraceStep; onOpen: () => void }) {
  return (
    <TableRow
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      className="cursor-pointer align-top"
      aria-label={`Открыть трейс ${shortTrace(step.trace_id)}: ${step.name}`}
    >
      <TableCell className="whitespace-nowrap tabular text-muted-foreground">
        <time dateTime={step.started_at}>{formatStamp(step.started_at)}</time>
      </TableCell>
      <TableCell>
        <KindBadge kind={step.kind} />
      </TableCell>
      <TableCell className="max-w-80">
        <span className="block truncate font-medium" title={step.name}>
          {step.name}
        </span>
        {step.error && (
          <span className="block truncate text-xs text-destructive" title={step.error}>
            {step.error}
          </span>
        )}
      </TableCell>
      <TableCell>
        <StatusBadge status={step.status} />
      </TableCell>
      <TableCell className="text-right tabular whitespace-nowrap">{formatMs(step.duration_ms)}</TableCell>
      <TableCell className="hidden text-right tabular md:table-cell">{step.attempt ?? '—'}</TableCell>
      <TableCell className="max-w-40 truncate">{step.client?.display_name || (step.client ? `№ ${step.client.id}` : '—')}</TableCell>
      <TableCell className="hidden max-w-40 truncate lg:table-cell">{step.actor || '—'}</TableCell>
      <TableCell className="hidden text-right tabular lg:table-cell">{step.http_status ?? '—'}</TableCell>
      <TableCell className="hidden font-mono text-xs md:table-cell">{shortTrace(step.trace_id)}</TableCell>
    </TableRow>
  );
}
