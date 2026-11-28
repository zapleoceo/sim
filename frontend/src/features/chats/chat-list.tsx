import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link, getRouteApi } from '@tanstack/react-router';
import { MessagesSquare, SearchX } from 'lucide-react';
import { ChannelIcon } from '@/components/channel-icon';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { SearchInput } from '@/components/search-input';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { PersonaFilter } from '@/features/persona/persona-filter';
import { usePersonaList } from '@/features/persona/use-persona-list';
import { useInView } from '@/hooks/use-in-view';
import { formatShort } from '@/lib/format';
import { qk } from '@/lib/query';
import type { ChannelKind, ChatRow } from '@/lib/types';
import type { ChatsSearch } from '@/router';
import { clientName, cn } from '@/lib/utils';
import { CHATS_PAGE, chatsApi } from './api';
import { emptyChatsText } from './chat-list-logic';
import { ChatAvatar, OperatorBadge, UnreadBadge } from './chat-row-parts';
import { isPersonOf, needsOperator, personMessengers, replyChatId, unreadTotal } from './person-logic';
import { ChatTable } from './chat-table';
import { PersonaStatusPill } from './persona-status';
import { STATUS_REFRESH_MS } from './persona-status-logic';
import { SourceFilter } from './source-filter';
import { parseSources, sourcesParam } from './sources-logic';
import { lastMessagePreview } from './timeline-logic';

const route = getRouteApi('/_app/chats');

type Filters = Omit<ChatsSearch, 'id'>;

function ChatListItem({ chat, active, showPersona, filters }: {
  chat: ChatRow;
  active: boolean;
  showPersona: boolean;
  /** The list's search and filters: opening a chat keeps them in the address. */
  filters: Filters;
}) {
  const name = clientName(chat.client);
  const unreadCount = unreadTotal(chat);
  const unread = unreadCount > 0;
  const messengers = personMessengers(chat);
  return (
    <li>
      <Link
        to="/chats"
        search={{ ...filters, id: replyChatId(chat) }}
        aria-current={active ? 'true' : undefined}
        className={cn(
          'flex items-center gap-3 border-b px-3 py-3 transition-colors hover:bg-accent/60 focus-visible:bg-accent focus-visible:outline-none',
          unread && 'shadow-[inset_3px_0_0_var(--primary)]',
          active && 'bg-accent',
        )}
      >
        <ChatAvatar chat={chat} />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-baseline gap-2">
            <span className={cn('min-w-0 truncate text-sm', unread ? 'font-semibold' : 'font-medium')}>{name}</span>
            {/* More than one messenger: all of them after the name (the avatar shows the latest). */}
            <span className="flex flex-1 items-center gap-1">
              {messengers.length > 1 && messengers.map((kind) => <ChannelIcon key={kind} kind={kind} />)}
            </span>
            <time
              className={cn('flex-none text-[11px] tabular', unread ? 'font-medium text-primary' : 'text-muted-foreground')}
              dateTime={chat.last_message_at ?? undefined}
            >
              {formatShort(chat.last_message_at)}
            </time>
          </span>
          <span className="flex items-center gap-2">
            <span className={cn('min-w-0 flex-1 truncate text-xs', unread ? 'text-foreground' : 'text-muted-foreground')}>
              {lastMessagePreview(chat.last_message)}
            </span>
            <UnreadBadge count={unreadCount} />
          </span>
          <span className="flex min-w-0 items-center gap-2">
            <span className="flex min-w-0 flex-1">
              <PersonaStatusPill status={chat.persona_status} />
            </span>
            {needsOperator(chat) && <OperatorBadge />}
            {showPersona && chat.persona && (
              <span className="flex-none truncate text-[11px] text-muted-foreground">
                <span className="sr-only">Отвечает </span>
                {chat.persona.name}
              </span>
            )}
          </span>
        </span>
      </Link>
    </li>
  );
}

function LoadingRows() {
  return (
    <ul aria-label="Загрузка чатов" className="flex flex-col">
      {Array.from({ length: 8 }, (_, i) => (
        <li key={i} className="flex items-center gap-3 border-b px-3 py-2.5">
          <Skeleton className="size-[42px] rounded-full" />
          <span className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-3 w-3/4" />
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The chats, newest activity first, more pages on scroll, with search, persona and source
 * filters. `table` (no chat open on a wide screen) shows them as one full-width table; `list`
 * (a chat is open, or a phone) as the narrow column of cards. The open chat, the search and the
 * filters live in the address (`/chats?id=5&q=анна&persona=2&source=whatsapp,instagram`).
 */
export function ChatList({ layout = 'list' }: { layout?: 'list' | 'table' }) {
  const { id: activeId, q = '', persona: personaId = 0, source = '' } = route.useSearch();
  const navigate = route.useNavigate();
  const sources = parseSources(source);
  const filters: Filters = {
    ...(q ? { q } : {}), ...(personaId ? { persona: personaId } : {}), ...(source ? { source } : {}),
  };
  const setFilters = (next: Filters): void =>
    void navigate({ search: (prev) => ({ ...prev, ...next }), replace: true });
  const setSources = (kinds: ChannelKind[]): void => setFilters({ source: sourcesParam(kinds) });
  const { rows: personaRows } = usePersonaList();
  const showPersona = personaRows.length > 1 && personaId === 0;
  const counts = useQuery({
    queryKey: qk.chatSources(personaId),
    queryFn: () => chatsApi.sources(personaId),
    refetchInterval: STATUS_REFRESH_MS,
  });
  const chats = useInfiniteQuery({
    queryKey: qk.chatList(q, personaId, source),
    queryFn: ({ pageParam }) => chatsApi.list(q, pageParam, personaId, source),
    initialPageParam: null as string | null,
    getNextPageParam: (last) =>
      last.items.length < CHATS_PAGE ? undefined : (last.items[last.items.length - 1]?.last_message_at ?? undefined),
    // «Статус Марины» moves with time (she becomes free, a promised hour comes).
    refetchInterval: STATUS_REFRESH_MS,
  });
  const items = chats.data?.pages.flatMap((p) => p.items) ?? [];
  const canLoadMore = chats.hasNextPage && !chats.isFetchingNextPage;
  const sentinel = useInView<HTMLDivElement>(() => void chats.fetchNextPage(), canLoadMore);
  const empty = emptyChatsText({ q, sources, persona: personaId });
  const reset = (): void => setFilters({ q: undefined, persona: undefined, source: undefined });
  const wide = layout === 'table';

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={cn('flex flex-none flex-col gap-2 border-b p-3', wide && 'gap-3 px-4 lg:flex-row lg:flex-wrap lg:items-center')}>
        <SearchInput
          value={q}
          onChange={(text) => setFilters({ q: text || undefined })}
          label="Поиск по чатам"
          placeholder="Имя, username или текст"
          className={wide ? 'lg:w-72' : undefined}
        />
        {personaRows.length > 1 && (
          <div className={wide ? 'lg:w-48' : undefined}>
            <PersonaFilter value={personaId} onChange={(id) => setFilters({ persona: id || undefined })} personas={personaRows} />
          </div>
        )}
        <SourceFilter value={sources} onChange={setSources} counts={counts.data?.items} className={wide ? 'lg:ml-auto lg:overflow-visible' : undefined} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        {chats.isPending && <LoadingRows />}
        {chats.isError && <ErrorState onRetry={() => void chats.refetch()} />}
        {chats.isSuccess && items.length === 0 && (
          <EmptyState icon={empty.filtered ? SearchX : MessagesSquare} title={empty.title} hint={empty.hint}>
            {empty.filtered ? (
              <Button variant="outline" size="sm" onClick={reset}>
                Сбросить фильтры
              </Button>
            ) : (
              <Button variant="outline" size="sm" asChild>
                <Link to="/personas">Подключить мессенджер</Link>
              </Button>
            )}
          </EmptyState>
        )}
        {items.length > 0 && wide && <ChatTable items={items} showPersona={showPersona} filters={filters} />}
        {items.length > 0 && !wide && (
          <ul aria-label="Чаты" className="flex flex-col">
            {items.map((chat) => (
              <ChatListItem key={chat.id} chat={chat} active={isPersonOf(chat, activeId)} showPersona={showPersona} filters={filters} />
            ))}
          </ul>
        )}
        {chats.hasNextPage && (
          <div ref={sentinel} className="flex justify-center p-3">
            <Button variant="ghost" size="sm" disabled={chats.isFetchingNextPage} onClick={() => void chats.fetchNextPage()}>
              {chats.isFetchingNextPage ? 'Загружаю…' : 'Показать ещё'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
