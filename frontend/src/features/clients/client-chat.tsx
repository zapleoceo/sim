import { useQuery } from '@tanstack/react-query';
import { MessageCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { MessageBubble } from '@/components/message-bubble';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { groupByDay } from '@/features/inbox/inbox-logic';
import { activityApi } from '@/lib/api/activity';
import { qk } from '@/lib/query';

export const CHAT_PAGE = 30;

/**
 * The client's conversation across all channels as a calm chat: bubbles, day separators, the
 * latest messages at the bottom. No filters and no technical details (those live on «Активность»).
 * «Показать раньше» loads older messages; live events refresh it (see lib/live.ts).
 */
export function ClientChat({ clientId }: { clientId: number }) {
  const [limit, setLimit] = useState(CHAT_PAGE);
  const box = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: qk.clientChat(clientId, limit),
    queryFn: () => activityApi.messages({}, { limit, offset: 0 }, clientId),
    placeholderData: (prev) => prev,
  });
  const items = query.data ? [...query.data.items].reverse() : [];
  const groups = groupByDay(items);
  const newest = items.at(-1)?.id;

  useEffect(() => {
    // Scroll inside the chat box only: the page itself stays at the top (header visible).
    const el = box.current;
    if (el && limit === CHAT_PAGE) el.scrollTop = el.scrollHeight;
  }, [newest, limit]);

  if (query.isPending) {
    return (
      <div role="status" aria-label="Загружаем переписку…" className="flex flex-col gap-3">
        <Skeleton className="h-12 w-2/3 rounded-2xl" />
        <Skeleton className="ml-auto h-12 w-1/2 rounded-2xl" />
      </div>
    );
  }
  if (query.isError) return <ErrorState title="Не удалось загрузить переписку" onRetry={() => void query.refetch()} />;
  if (!items.length) return <EmptyState icon={MessageCircle} title="Переписки пока нет" className="py-10" />;

  return (
    <div ref={box} className="flex max-h-[70vh] flex-col gap-2 overflow-y-auto pr-1 scrollbar-thin">
      {(query.data?.total ?? 0) > items.length && (
        <Button variant="ghost" size="sm" className="self-center text-muted-foreground"
          disabled={query.isFetching} onClick={() => setLimit((n) => n + CHAT_PAGE)}>
          Показать раньше
        </Button>
      )}
      {groups.map((g) => (
        <div key={g.key} className="flex flex-col gap-2">
          <p className="my-2 text-center text-xs text-muted-foreground">{g.label}</p>
          {g.items.map((m) => <MessageBubble key={m.msg.id} vm={m} />)}
        </div>
      ))}
    </div>
  );
}
