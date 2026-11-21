import { useInfiniteQuery } from '@tanstack/react-query';
import { Link, getRouteApi } from '@tanstack/react-router';
import { AlertTriangle, MessagesSquare } from 'lucide-react';
import { Avatar } from '@/components/avatar';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { SearchInput } from '@/components/search-input';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { PersonaFilter } from '@/features/persona/persona-filter';
import { usePersonaList } from '@/features/persona/use-persona-list';
import { useInView } from '@/hooks/use-in-view';
import { formatShort } from '@/lib/format';
import { qk } from '@/lib/query';
import type { ChatRow } from '@/lib/types';
import type { ChatsSearch } from '@/router';
import { clientName, cn } from '@/lib/utils';
import { CHATS_PAGE, chatsApi } from './api';
import { PersonaStatusPill } from './persona-status';
import { STATUS_REFRESH_MS } from './persona-status-logic';
import { aiActive, lastMessagePreview } from './timeline-logic';

const route = getRouteApi('/_app/chats');

function ChatListItem({ chat, active, showPersona, filters }: {
  chat: ChatRow;
  active: boolean;
  showPersona: boolean;
  /** The list's search and persona filter: opening a chat keeps them in the address. */
  filters: Omit<ChatsSearch, 'id'>;
}) {
  const ai = aiActive(chat);
  const name = clientName(chat.client);
  return (
    <li>
      <Link
        to="/chats"
        search={{ ...filters, id: chat.id }}
        aria-current={active ? 'true' : undefined}
        className={cn(
          'flex items-center gap-3 border-b px-3 py-2.5 transition-colors hover:bg-accent/60 focus-visible:bg-accent focus-visible:outline-none',
          active && 'bg-accent',
        )}
      >
        <span className="relative flex-none">
          <Avatar name={name} size={42} />
          <span
            className={cn(
              'absolute right-0 bottom-0 size-3 rounded-full border-2 border-background',
              ai ? 'bg-success' : 'bg-muted-foreground/50',
            )}
            role="img"
            aria-label={ai ? 'ИИ отвечает' : 'ИИ выключен'}
            title={ai ? 'ИИ отвечает' : 'ИИ выключен'}
          />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-baseline gap-2">
            <span className={cn('min-w-0 flex-1 truncate text-sm', chat.unread_count > 0 ? 'font-semibold' : 'font-medium')}>
              {name}
            </span>
            <time className="flex-none text-[11px] text-muted-foreground tabular" dateTime={chat.last_message_at ?? undefined}>
              {formatShort(chat.last_message_at)}
            </time>
          </span>
          <PersonaStatusPill status={chat.persona_status} />
          {showPersona && chat.persona && (
            <span className="truncate text-[11px] text-muted-foreground">
              <span className="sr-only">Отвечает </span>
              {chat.persona.name}
            </span>
          )}
          <span className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{lastMessagePreview(chat.last_message)}</span>
            {chat.needs_operator && (
              <span className="inline-flex flex-none items-center gap-1 rounded-md bg-warning/15 px-1.5 py-0.5 text-[11px] font-medium text-warning">
                <AlertTriangle className="size-3" />
                нужен оператор
              </span>
            )}
            {chat.unread_count > 0 && (
              <span
                className="flex h-5 min-w-5 flex-none items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold text-primary-foreground tabular"
                aria-label={`Непрочитанных: ${chat.unread_count}`}
              >
                {chat.unread_count > 99 ? '99+' : chat.unread_count}
              </span>
            )}
          </span>
        </span>
      </Link>
    </li>
  );
}

/**
 * Left pane: search + persona filter + chats, newest activity first, more pages on scroll. Each
 * row names the persona that answers in that chat (when there is more than one persona). The
 * open chat, the search and the filter live in the address (`/chats?id=5&q=анна&persona=2`).
 */
export function ChatList() {
  const { id: activeId, q = '', persona: personaId = 0 } = route.useSearch();
  const navigate = route.useNavigate();
  const filters = { ...(q ? { q } : {}), ...(personaId ? { persona: personaId } : {}) };
  const setFilters = (next: { q?: string; persona?: number }): void =>
    void navigate({ search: (prev) => ({ ...prev, ...next }), replace: true });
  const { rows: personaRows } = usePersonaList();
  const showPersona = personaRows.length > 1 && personaId === 0;
  const chats = useInfiniteQuery({
    queryKey: qk.chatList(q, personaId),
    queryFn: ({ pageParam }) => chatsApi.list(q, pageParam, personaId),
    initialPageParam: null as string | null,
    getNextPageParam: (last) =>
      last.items.length < CHATS_PAGE ? undefined : (last.items[last.items.length - 1]?.last_message_at ?? undefined),
    // «Статус Марины» moves with time (she becomes free, a promised hour comes).
    refetchInterval: STATUS_REFRESH_MS,
  });
  const items = chats.data?.pages.flatMap((p) => p.items) ?? [];
  const canLoadMore = chats.hasNextPage && !chats.isFetchingNextPage;
  const sentinel = useInView<HTMLDivElement>(() => void chats.fetchNextPage(), canLoadMore);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex-none border-b p-3">
        <SearchInput
          value={q}
          onChange={(text) => setFilters({ q: text || undefined })}
          label="Поиск по чатам"
          placeholder="Имя, username или текст"
        />
        {personaRows.length > 1 && (
          <div className="mt-2">
            <PersonaFilter value={personaId} onChange={(id) => setFilters({ persona: id || undefined })} personas={personaRows} />
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        {chats.isPending && (
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
        )}
        {chats.isError && <ErrorState onRetry={() => void chats.refetch()} />}
        {chats.isSuccess && items.length === 0 && (
          <EmptyState
            icon={MessagesSquare}
            title={q ? 'Ничего не найдено' : 'Чатов пока нет'}
            hint={q ? 'Попробуйте другое имя или слово.' : 'Когда клиент напишет персонажу, чат появится здесь.'}
          />
        )}
        {items.length > 0 && (
          <ul aria-label="Чаты" className="flex flex-col">
            {items.map((chat) => (
              <ChatListItem key={chat.id} chat={chat} active={chat.id === activeId} showPersona={showPersona} filters={filters} />
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
