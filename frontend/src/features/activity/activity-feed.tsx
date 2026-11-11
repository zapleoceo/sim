import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { MessageCircle, RefreshCw, SearchX } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { EmptyState, ErrorState, InlineError } from '@/components/empty-state';
import { MessageCard } from '@/components/message-card';
import { costLabel, metric } from '@/components/message-parts';
import { Pager } from '@/components/pager';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useDebounced } from '@/hooks/use-debounced';
import { activityApi } from '@/lib/api/activity';
import type { PageRequest } from '@/lib/api/http';
import type { LlmActivity, MessageActivity } from '@/lib/activity-models';
import { useAuth } from '@/lib/auth';
import { dayKey, dayLabel, formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query';
import { EMPTY_ACTIVITY_FORM, toActivityFilter, type ActivityFormValues } from './activity-filters-logic';
import { ActivityFilters } from './activity-filters';

const FIRST_PAGE: PageRequest = { limit: 25, offset: 0 };

/**
 * Messages in display order with day separators. A client's feed comes newest-first per page from the API
 * and is shown oldest-to-newest inside the page.
 */
export function feedItems(items: MessageActivity[], clientScoped: boolean): { message: MessageActivity; day: string }[] {
  const list = clientScoped ? [...items].reverse() : items;
  return list.map((message, i) => ({
    message,
    day: i === 0 || dayKey(list[i - 1].created_at) !== dayKey(message.created_at) ? dayLabel(message.created_at) : '',
  }));
}

function LlmCard({ call }: { call: LlmActivity }) {
  return (
    <article className="flex flex-col gap-2 rounded-xl border bg-card p-4 text-sm shadow-xs [overflow-wrap:anywhere]">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <strong className="flex items-center gap-2 font-medium">
          Вызов № {call.id}
          <Badge variant={call.has_error ? 'destructive' : 'success'}>{call.has_error ? 'Ошибка' : 'Успешно'}</Badge>
        </strong>
        <time dateTime={call.created_at} className="text-xs text-muted-foreground">
          {formatDateTime(call.created_at)}
        </time>
      </header>
      <p>
        {call.workflow} · {call.capability}
      </p>
      <p>
        Модель: {call.model || '—'} · Провайдер: {call.provider || '—'}
      </p>
      <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground tabular">
        <span>Вход: {metric(call.tokens_in)}</span>
        <span>Выход: {metric(call.tokens_out)}</span>
        <span>Из кеша: {metric(call.cached_tokens)}</span>
        <span>{costLabel(call.cost_usd)}</span>
        <span>{call.latency_ms} мс</span>
      </p>
      <p className="text-xs text-muted-foreground">Завершение: {call.finish_reason || '—'}</p>
      {call.channel && (
        <p>
          Канал: {call.channel.name} · {call.channel.account_label || '—'}
        </p>
      )}
      {call.persona && <p>Персонаж: {call.persona.name}</p>}
      <footer className="flex flex-wrap items-center gap-3 text-sm">
        {call.client && (
          <Link to="/clients/$id" params={{ id: String(call.client.id) }} className="text-primary hover:underline">
            {call.client.display_name}
          </Link>
        )}
        {call.dialog_id !== null ? (
          <Button asChild variant="ghost" size="sm" className="ml-auto">
            <Link to="/inbox" search={{ d: call.dialog_id }}>
              Открыть диалог
            </Link>
          </Button>
        ) : (
          <span className="text-xs text-muted-foreground">Без привязки к диалогу</span>
        )}
      </footer>
    </article>
  );
}

/** Activity log: messages (all channels or one client) or LLM calls (owner only). */
export function ActivityFeed({
  clientId,
  channelId = null,
  llm = false,
}: {
  clientId?: number;
  channelId?: number | null;
  llm?: boolean;
}) {
  const { isOwner } = useAuth();
  const allowed = !llm || isOwner;
  const clientScoped = clientId !== undefined;
  const [form, setFormState] = useState<ActivityFormValues>({ ...EMPTY_ACTIVITY_FORM, channel_id: channelId });
  const [page, setPage] = useState<PageRequest>(FIRST_PAGE);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  const setForm = (v: ActivityFormValues): void => {
    setFormState(v);
    setPage((p) => ({ ...p, offset: 0 }));
  };

  // Channel from the URL (e.g. «Активность канала») pre-fills the filter.
  useEffect(() => {
    setFormState((f) => ({ ...f, channel_id: channelId }));
    setPage((p) => ({ ...p, offset: 0 }));
  }, [channelId]);

  const settled = useDebounced(form, 250);
  const filter = useMemo(() => toActivityFilter(settled, { llm, clientScoped }), [settled, llm, clientScoped]);
  const formValid = toActivityFilter(form, { llm, clientScoped }) !== null;

  const query = useQuery({
    queryKey: qk.activity(llm ? 'llm' : 'messages', { filter, page, clientId }),
    queryFn: async () => {
      if (!filter) throw new Error('invalid filter');
      if (llm) {
        const p = await activityApi.llm(filter, page);
        return { total: p.total, calls: p.items, messages: [] as MessageActivity[] };
      }
      const p = await activityApi.messages(filter, page, clientId);
      return { total: p.total, calls: [] as LlmActivity[], messages: p.items };
    },
    enabled: allowed && filter !== null,
  });

  if (!allowed) return <p role="alert">Журнал вызовов доступен только владельцу.</p>;

  const refresh = (): void => {
    if (page.offset !== 0) setPage((p) => ({ ...p, offset: 0 }));
    else void query.refetch();
  };

  const data = query.data;
  const messages = data ? feedItems(data.messages, clientScoped) : [];

  return (
    <div className="flex flex-col gap-4">
      <ActivityFilters values={form} onChange={setForm} llm={llm} clientScoped={clientScoped} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {clientScoped
            ? `${page.offset === 0 ? 'Последние сообщения' : 'Более ранние сообщения'} · внутри страницы от старых к новым. `
            : 'Сначала новые записи. '}
          Время: {timezone}.
        </p>
        <Button variant="outline" size="sm" onClick={refresh} disabled={query.isFetching || !formValid}>
          <RefreshCw />
          Обновить
        </Button>
      </div>

      <section aria-busy={query.isFetching} aria-label="Результаты журнала" className="flex flex-col gap-3">
        {!formValid ? (
          <InlineError>Исправьте фильтры, чтобы загрузить записи.</InlineError>
        ) : query.isPending ? (
          <div role="status" aria-label="Загружаем записи…" className="flex flex-col gap-3">
            <Skeleton className="h-28 w-full rounded-xl" />
            <Skeleton className="h-28 w-full rounded-xl" />
          </div>
        ) : query.isError ? (
          <ErrorState title="Не удалось загрузить записи" onRetry={() => void query.refetch()} />
        ) : data ? (
          <>
            <p role="status" className="text-xs text-muted-foreground">
              Найдено: {data.total}
            </p>
            {llm ? (
              data.calls.length ? (
                data.calls.map((call) => <LlmCard key={call.id} call={call} />)
              ) : (
                <EmptyState icon={SearchX} title="Вызовов не найдено" hint="Измените фильтры или дождитесь новых обращений к модели." />
              )
            ) : messages.length ? (
              messages.map(({ message, day }) => (
                <div key={message.id} className="flex flex-col gap-3">
                  {day && <p className="pt-2 text-xs font-medium text-muted-foreground">{day}</p>}
                  <MessageCard message={message} />
                </div>
              ))
            ) : (
              <EmptyState
                icon={MessageCircle}
                title="Сообщений не найдено"
                hint="Измените фильтры. Новые сообщения появятся после общения в подключённых каналах."
              />
            )}
            {clientScoped && data.total > page.limit && (
              <p className="text-xs text-muted-foreground">Следующая страница — более ранние сообщения; первая — последние.</p>
            )}
            {data.total > 0 && <Pager page={page} total={data.total} onChange={setPage} label="Страницы записей" />}
          </>
        ) : null}
      </section>
    </div>
  );
}
