import { useQuery } from '@tanstack/react-query';
import { EyeOff } from 'lucide-react';
import { Tip } from '@/components/ui/tooltip';
import { useNow } from '@/hooks/use-now';
import { qk } from '@/lib/query';
import type { PersonaStatus } from '@/lib/types';
import { cn } from '@/lib/utils';
import { chatsApi } from './api';
import { STATUS_REFRESH_MS, TEAM_ONLY, TONE_CLASS, TONE_DOT, statusText, untilLine } from './persona-status-logic';

/**
 * «Статус Марины» in a chat-list row: one colored line under the client's name with the reason
 * after the title (cut to the row; readable on touch and by a screen reader, not only on hover),
 * the whole text in the tooltip. Team-only (docs/attention.md, «Статус для команды»).
 */
export function PersonaStatusPill({ status }: { status: PersonaStatus }) {
  return (
    <Tip
      label={
        <>
          {statusText(status)}
          <span className="mt-1 block opacity-75">{TEAM_ONLY}</span>
        </>
      }
    >
      <span
        data-tone={status.tone}
        className={cn(
          'inline-flex max-w-full min-w-0 items-center gap-1.5 self-start rounded-full py-0.5 text-[11px] leading-4',
          // The quiet «ok» has no background: no padding either, it lines up with the text above.
          status.tone === 'ok' ? 'px-0' : 'px-2 font-medium',
          TONE_CLASS[status.tone],
        )}
      >
        <span aria-hidden className={cn('size-1.5 flex-none rounded-full', TONE_DOT[status.tone])} />
        <span className="sr-only">Статус для команды: </span>
        <span className="truncate">
          {status.title}
          {status.detail && <span className="font-normal opacity-80"> · {status.detail}</span>}
        </span>
      </span>
    </Tip>
  );
}

/**
 * The fuller strip under the conversation header: title, reason and when the pause ends
 * («до 20:40 по её времени · через 12 мин»). Re-read on the chat's live events
 * (`qk.chatStatus`) and every 30 s; the relative time ticks with it.
 */
export function PersonaStatusStrip({ chatId, initial }: { chatId: number; initial: PersonaStatus }) {
  const query = useQuery({
    queryKey: qk.chatStatus(chatId),
    queryFn: () => chatsApi.status(chatId),
    placeholderData: initial,
    refetchInterval: STATUS_REFRESH_MS,
  });
  const now = useNow(STATUS_REFRESH_MS);
  const status = query.data ?? initial;
  const until = untilLine(status, now);

  return (
    <div
      role="status"
      data-tone={status.tone}
      className={cn('flex flex-none items-start gap-2 border-b px-3 py-1.5 text-xs sm:px-4', TONE_CLASS[status.tone])}
    >
      <span aria-hidden className={cn('mt-1 size-2 flex-none rounded-full', TONE_DOT[status.tone])} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="sr-only">Статус для команды: </span>
          <span className="font-semibold">{status.title}</span>
          {until && <span className="text-foreground/70 tabular">{until}</span>}
        </p>
        {status.detail && <p className="break-words text-foreground/70">{status.detail}</p>}
      </div>
      <Tip label={TEAM_ONLY}>
        <span
          tabIndex={0}
          aria-label={TEAM_ONLY}
          className="mt-0.5 inline-flex flex-none items-center gap-1 rounded text-foreground/60 focus-visible:outline-2 focus-visible:outline-ring"
        >
          <EyeOff className="size-3.5" aria-hidden />
          <span aria-hidden className="max-sm:hidden">только команде</span>
        </span>
      </Tip>
    </div>
  );
}
