import { getRouteApi } from '@tanstack/react-router';
import { MessagesSquare } from 'lucide-react';
import { EmptyState } from '@/components/empty-state';
import { SINGLE_PANE_QUERY, useMediaQuery } from '@/hooks/use-media-query';
import { cn } from '@/lib/utils';
import { ChatList } from './chat-list';
import { Conversation } from './conversation';

const route = getRouteApi('/_app/chats');

/**
 * Two panes (list | conversation) from 1024 px; below that one pane at a time,
 * the open chat lives in the URL (`/chats?id=5`) so «back» returns to the list. The list's search
 * and persona filter live there too (`&q=…&persona=2`), so they survive opening a chat.
 */
export function ChatsPage() {
  const { id } = route.useSearch();
  const navigate = route.useNavigate();
  const singlePane = useMediaQuery(SINGLE_PANE_QUERY);
  const chatId = id ?? null;
  const showList = !singlePane || chatId === null;
  const showChat = !singlePane || chatId !== null;
  // Back to the list keeps its filters: only the open chat leaves the address.
  const back = (): void => void navigate({ search: ({ id: _open, ...filters }) => filters });

  return (
    <div data-fit-viewport className="flex h-[calc(var(--app-vh,100dvh)-var(--bottom-nav))] min-h-0">
      {showList && (
        <aside
          aria-label="Список чатов"
          className={cn('h-full min-h-0 flex-none border-r', singlePane ? 'w-full' : 'w-[360px] xl:w-[400px]')}
        >
          <ChatList />
        </aside>
      )}
      {showChat && (
        <section aria-label="Переписка" className="h-full min-h-0 min-w-0 flex-1">
          {chatId !== null ? (
            <Conversation key={chatId} chatId={chatId} onBack={singlePane ? back : undefined} />
          ) : (
            <EmptyState className="h-full" icon={MessagesSquare} title="Выберите чат" hint="Слева — все диалоги персонажа." />
          )}
        </section>
      )}
    </div>
  );
}
