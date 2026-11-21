import { getRouteApi, useNavigate } from '@tanstack/react-router';
import { MessagesSquare } from 'lucide-react';
import { EmptyState } from '@/components/empty-state';
import { SINGLE_PANE_QUERY, useMediaQuery } from '@/hooks/use-media-query';
import { cn } from '@/lib/utils';
import { ChatList } from './chat-list';
import { Conversation } from './conversation';

const route = getRouteApi('/_app/chats');

/**
 * Two panes (list | conversation) from 1024 px; below that one pane at a time,
 * the open chat lives in the URL (`/chats?id=5`) so «back» returns to the list.
 */
export function ChatsPage() {
  const { id } = route.useSearch();
  const navigate = useNavigate();
  const singlePane = useMediaQuery(SINGLE_PANE_QUERY);
  const chatId = id ?? null;
  const showList = !singlePane || chatId === null;
  const showChat = !singlePane || chatId !== null;

  return (
    <div data-fit-viewport className="flex h-[calc(var(--app-vh,100dvh)-var(--bottom-nav))] min-h-0">
      {showList && (
        <aside
          aria-label="Список чатов"
          className={cn('h-full min-h-0 flex-none border-r', singlePane ? 'w-full' : 'w-[360px] xl:w-[400px]')}
        >
          <ChatList activeId={chatId} />
        </aside>
      )}
      {showChat && (
        <section aria-label="Переписка" className="h-full min-h-0 min-w-0 flex-1">
          {chatId !== null ? (
            <Conversation
              key={chatId}
              chatId={chatId}
              onBack={singlePane ? () => void navigate({ to: '/chats', search: {} }) : undefined}
            />
          ) : (
            <EmptyState className="h-full" icon={MessagesSquare} title="Выберите чат" hint="Слева — все диалоги персонажа." />
          )}
        </section>
      )}
    </div>
  );
}
