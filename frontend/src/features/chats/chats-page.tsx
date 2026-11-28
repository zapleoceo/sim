import { getRouteApi } from '@tanstack/react-router';
import { SINGLE_PANE_QUERY, useMediaQuery } from '@/hooks/use-media-query';
import { cn } from '@/lib/utils';
import { ChatList } from './chat-list';
import { Conversation } from './conversation';

const route = getRouteApi('/_app/chats');

/**
 * With no chat open the chats fill the page: a full-width table from 1024 px, cards on a phone.
 * An open chat (`/chats?id=5`) gets two panes (list | conversation) from 1024 px, below that one
 * pane at a time, so «back» returns to the list. The list's search, persona and source filters
 * live in the address too (`&q=…&persona=2&source=whatsapp`), so they survive opening a chat.
 */
export function ChatsPage() {
  const { id } = route.useSearch();
  const navigate = route.useNavigate();
  const singlePane = useMediaQuery(SINGLE_PANE_QUERY);
  const chatId = id ?? null;
  const showList = !singlePane || chatId === null;
  // Back to the list keeps its filters: only the open chat leaves the address.
  const back = (): void => void navigate({ search: ({ id: _open, ...filters }) => filters });

  return (
    <div data-fit-viewport className="flex h-[calc(var(--app-vh,100dvh)-var(--bottom-nav))] min-h-0">
      {showList && (
        <aside
          aria-label="Список чатов"
          className={cn(
            'h-full min-h-0 flex-none',
            chatId === null || singlePane ? 'w-full' : 'w-[360px] border-r xl:w-[400px]',
          )}
        >
          <ChatList layout={chatId === null && !singlePane ? 'table' : 'list'} />
        </aside>
      )}
      {chatId !== null && (
        <section aria-label="Переписка" className="h-full min-h-0 min-w-0 flex-1">
          <Conversation key={chatId} chatId={chatId} onBack={singlePane ? back : undefined} />
        </section>
      )}
    </div>
  );
}
