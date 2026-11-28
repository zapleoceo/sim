import { useInfiniteQuery } from '@tanstack/react-query';
import { MessageSquareDashed } from 'lucide-react';
import { Fragment, useState } from 'react';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { MessengerChange, messengerChanged } from '@/features/chats/messenger-change';
import { ReplyRunSheet } from '@/features/chats/reply-run-sheet';
import { TimelineEntry } from '@/features/chats/timeline-entry';
import { useInView } from '@/hooks/use-in-view';
import { dayKey, dayLabel } from '@/lib/format';
import { qk } from '@/lib/query';
import { CLIENT_TIMELINE_PAGE, leadsApi } from './api';

/** The day chip of the feed. */
function DayChip({ at }: { at: string }) {
  return (
    <div className="flex justify-center py-1">
      <span className="rounded-full bg-muted px-3 py-0.5 text-xs text-muted-foreground">{dayLabel(at)}</span>
    </div>
  );
}

/**
 * «Лента» of the lead card: the journal of every chat of the client in one stream (Telegram,
 * WhatsApp, Instagram), newest at the bottom like a chat. «→ WhatsApp» marks where the messenger
 * changes (the same separator as in /chats), each message has its messenger's icon, a chip names
 * the day; older pages load on scroll up.
 */
export function ClientFeed({ clientId }: { clientId: number }) {
  const [runId, setRunId] = useState<number | null>(null);
  const feed = useInfiniteQuery({
    queryKey: qk.clientTimeline(clientId),
    queryFn: ({ pageParam }) => leadsApi.timeline(clientId, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) =>
      last.items.length < CLIENT_TIMELINE_PAGE ? undefined : (last.items[last.items.length - 1]?.id ?? undefined),
  });
  const items = feed.data?.pages.flatMap((p) => p.items) ?? []; // newest first
  const canLoadMore = feed.hasNextPage && !feed.isFetchingNextPage;
  const sentinel = useInView<HTMLDivElement>(() => void feed.fetchNextPage(), canLoadMore);

  if (feed.isPending) {
    return (
      <div className="flex flex-col gap-3 rounded-xl border bg-card p-4" aria-label="Загрузка ленты">
        <Skeleton className="h-10 w-2/3 self-start rounded-2xl" />
        <Skeleton className="h-14 w-1/2 self-end rounded-2xl" />
      </div>
    );
  }
  if (feed.isError) return <ErrorState onRetry={() => void feed.refetch()} />;
  if (items.length === 0) return <EmptyState icon={MessageSquareDashed} title="Переписки пока нет" />;

  return (
    <>
      <div
        role="log"
        aria-label="Лента по всем мессенджерам"
        className="flex h-[calc(var(--app-vh,100dvh)-16rem)] min-h-80 flex-col-reverse overflow-y-auto rounded-xl border bg-card px-3 py-3 scrollbar-thin sm:px-6"
      >
        <div className="flex flex-col-reverse gap-2.5">
          {items.map((item, i) => {
            const older = items[i + 1];
            const day = !older || dayKey(older.created_at) !== dayKey(item.created_at);
            return (
              <Fragment key={item.id}>
                <TimelineEntry item={item} onExplain={setRunId} channel={item.channel?.kind} />
                {messengerChanged(item, older) && <MessengerChange kind={item.channel?.kind} />}
                {day && <DayChip at={item.created_at} />}
              </Fragment>
            );
          })}
        </div>
        {feed.hasNextPage && (
          <div ref={sentinel} className="flex justify-center p-2">
            <Button variant="ghost" size="sm" disabled={feed.isFetchingNextPage} onClick={() => void feed.fetchNextPage()}>
              {feed.isFetchingNextPage ? 'Загружаю…' : 'Показать раньше'}
            </Button>
          </div>
        )}
      </div>
      <ReplyRunSheet runId={runId} onClose={() => setRunId(null)} />
    </>
  );
}
