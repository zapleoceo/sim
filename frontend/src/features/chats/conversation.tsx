import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ArrowLeft, CheckCircle2, MessageSquareDashed, EllipsisVertical, StickyNote } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { ChannelIcon } from '@/components/channel-icon';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { MutationSwitch } from '@/components/mutation-switch';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { PassportSheet } from '@/features/leads/passport-summary';
import { useInView } from '@/hooks/use-in-view';
import { dayKey, dayLabel } from '@/lib/format';
import { qk } from '@/lib/query';
import type { ChatDetail, TimelineItem } from '@/lib/types';
import { clientName } from '@/lib/utils';
import { TIMELINE_PAGE, chatsApi, invalidateChat } from './api';
import { ChatFlagMenuItem, ChatFlagSwitch } from './chat-flag-switch';
import { Composer } from './composer';
import { PersonaStatusStrip } from './persona-status';
import { ReplyRunSheet } from './reply-run-sheet';
import { TimelineEntry } from './timeline-entry';

function NoteDialog({ chatId, open, onOpenChange }: { chatId: number; open: boolean; onOpenChange: (v: boolean) => void }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const add = useMutation({
    mutationFn: () => chatsApi.addNote(chatId, text.trim()),
    onSuccess: () => {
      toast.success('Заметка добавлена — агент увидит её в следующем ответе');
      setText('');
      onOpenChange(false);
      void queryClient.invalidateQueries({ queryKey: qk.timeline(chatId) });
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Заметка для агента</DialogTitle>
          <DialogDescription>Клиент её не увидит. Агент учтёт её в следующих ответах этого чата.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) add.mutate();
          }}
        >
          <label className="flex flex-col gap-1.5">
            <span className="sr-only">Текст заметки</span>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Например: не предлагай встречу до пятницы"
              className="min-h-24"
              autoFocus
            />
          </label>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
            <Button type="submit" disabled={!text.trim() || add.isPending}>Добавить</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Below 640 px the header keeps the name, «Паспорт» and «ИИ в чате»; «Заметка», the chat flags and
 * «Оператор разобрался» move into this menu (a dot on it while an operator is needed).
 */
function ChatHeaderMenu({ chat, onNote, onDone, doneBusy }: {
  chat: ChatDetail;
  onNote: () => void;
  onDone: () => void;
  doneBusy: boolean;
}) {
  return (
    // Not modal: «Заметка» opens a dialog right after the menu closes.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="relative sm:hidden" aria-label="Ещё действия с чатом">
          <EllipsisVertical />
          {chat.needs_operator && <span aria-hidden className="absolute top-1.5 right-1.5 size-2 rounded-full bg-warning" />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {chat.needs_operator && (
          <DropdownMenuItem disabled={doneBusy} onSelect={onDone}>
            <CheckCircle2 />
            Оператор разобрался
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={onNote}>
          <StickyNote />
          Заметка
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <ChatFlagMenuItem chatId={chat.id} flag="attention_instant" enabled={chat.attention_instant} />
        <ChatFlagMenuItem chatId={chat.id} flag="no_refusals" enabled={chat.no_refusals} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ChatHeader({ chat, onBack }: { chat: ChatDetail; onBack?: () => void }) {
  const queryClient = useQueryClient();
  const [noteOpen, setNoteOpen] = useState(false);
  const ai = useMutation({
    mutationFn: (enabled: boolean) => chatsApi.setAi(chat.id, enabled),
    onSuccess: (_row, enabled) => {
      toast.success(enabled ? 'ИИ включён в этом чате' : 'ИИ выключен в этом чате');
      invalidateChat(queryClient, chat.id);
    },
  });
  const done = useMutation({
    mutationFn: () => chatsApi.operatorDone(chat.id),
    onSuccess: () => {
      toast.success('Отметка «нужен оператор» снята');
      invalidateChat(queryClient, chat.id);
    },
  });
  const name = clientName(chat.client);

  return (
    <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b px-2 py-2 sm:px-4">
      {onBack && (
        <Button variant="ghost" size="icon" onClick={onBack} aria-label="К списку чатов">
          <ArrowLeft />
        </Button>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <Link
          to="/leads/$clientId"
          params={{ clientId: String(chat.client.id) }}
          className="truncate font-semibold hover:underline"
        >
          {name}
        </Link>
        <span className="flex min-w-0 items-center gap-1 truncate text-xs text-muted-foreground">
          {chat.channel && <ChannelIcon kind={chat.channel.kind} />}
          {chat.persona && <span className="font-medium text-foreground/80">отвечает {chat.persona.name} · </span>}
          {chat.client.username ? `@${chat.client.username}` : 'без username'}
          {!chat.client.ai_enabled && ' · ИИ выключен у клиента'}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <PassportSheet client={chat.client} />
        <div className="flex flex-wrap items-center gap-2 max-sm:hidden">
          {chat.needs_operator && (
            <Button variant="outline" size="sm" onClick={() => done.mutate()} disabled={done.isPending}>
              <CheckCircle2 />
              Оператор разобрался
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => setNoteOpen(true)}>
            <StickyNote />
            Заметка
          </Button>
          <ChatFlagSwitch chatId={chat.id} flag="attention_instant" enabled={chat.attention_instant} />
          <ChatFlagSwitch chatId={chat.id} flag="no_refusals" enabled={chat.no_refusals} />
        </div>
        <MutationSwitch
          checked={chat.ai_enabled}
          pending={ai.isPending}
          pendingValue={ai.variables}
          onCheckedChange={(v) => ai.mutate(v)}
          label="ИИ в чате"
        />
        <ChatHeaderMenu chat={chat} onNote={() => setNoteOpen(true)} onDone={() => done.mutate()} doneBusy={done.isPending} />
      </div>
      <NoteDialog chatId={chat.id} open={noteOpen} onOpenChange={setNoteOpen} />
    </header>
  );
}

/**
 * Timeline, newest at the bottom. The scroller is `flex-col-reverse`, so it opens at the bottom,
 * stays there when new events arrive, and keeps its place when older pages load at the top.
 */
function Timeline({ chatId, onExplain }: { chatId: number; onExplain: (runId: number) => void }) {
  const timeline = useInfiniteQuery({
    queryKey: qk.timeline(chatId),
    queryFn: ({ pageParam }) => chatsApi.timeline(chatId, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) =>
      last.items.length < TIMELINE_PAGE ? undefined : (last.items[last.items.length - 1]?.id ?? undefined),
  });
  const items: TimelineItem[] = timeline.data?.pages.flatMap((p) => p.items) ?? []; // newest first
  const canLoadMore = timeline.hasNextPage && !timeline.isFetchingNextPage;
  const sentinel = useInView<HTMLDivElement>(() => void timeline.fetchNextPage(), canLoadMore);

  if (timeline.isPending) {
    return (
      <div className="flex flex-1 flex-col justify-end gap-3 p-4" aria-label="Загрузка переписки">
        <Skeleton className="h-10 w-2/3 self-start rounded-2xl" />
        <Skeleton className="h-14 w-1/2 self-end rounded-2xl" />
        <Skeleton className="h-10 w-3/5 self-start rounded-2xl" />
      </div>
    );
  }
  if (timeline.isError) return <div className="flex-1"><ErrorState onRetry={() => void timeline.refetch()} /></div>;
  if (items.length === 0) {
    return <EmptyState className="flex-1" icon={MessageSquareDashed} title="Переписки пока нет" />;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col-reverse overflow-y-auto px-3 py-3 scrollbar-thin sm:px-6" role="log" aria-label="Переписка">
      <div className="flex flex-col-reverse gap-2.5">
        {items.map((item, i) => {
          const older = items[i + 1];
          const newDay = !older || dayKey(older.created_at) !== dayKey(item.created_at);
          return (
            <Fragment key={item.id}>
              <TimelineEntry item={item} onExplain={onExplain} />
              {newDay && (
                <div className="sticky top-0 z-10 flex justify-center py-1">
                  <span className="rounded-full bg-background/90 px-3 py-0.5 text-xs text-muted-foreground shadow-xs backdrop-blur">
                    {dayLabel(item.created_at)}
                  </span>
                </div>
              )}
            </Fragment>
          );
        })}
        {timeline.hasNextPage && (
          <div ref={sentinel} className="flex justify-center py-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={timeline.isFetchingNextPage}
              onClick={() => void timeline.fetchNextPage()}
            >
              {timeline.isFetchingNextPage ? 'Загружаю…' : 'Показать раньше'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Right pane: header, timeline, composer; marks the chat read while it is open. */
export function Conversation({ chatId, onBack }: { chatId: number; onBack?: () => void }) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<number | null>(null);
  const chat = useQuery({ queryKey: qk.chat(chatId), queryFn: () => chatsApi.get(chatId) });
  const unread = chat.data?.unread_count ?? 0;

  useEffect(() => {
    if (unread <= 0) return;
    chatsApi
      .markRead(chatId)
      .then(() => queryClient.invalidateQueries({ queryKey: qk.chatLists }))
      .catch(() => undefined); // not worth a toast: the badge simply stays
  }, [chatId, unread, queryClient]);

  if (chat.isPending) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-3 border-b p-3">
          <Skeleton className="h-5 w-40" />
        </div>
        <div className="flex-1" />
      </div>
    );
  }
  if (chat.isError) {
    return (
      <div className="flex h-full flex-col">
        {onBack && (
          <div className="border-b p-2">
            <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft />К списку</Button>
          </div>
        )}
        <ErrorState title="Чат не открылся" onRetry={() => void chat.refetch()} />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChatHeader chat={chat.data} onBack={onBack} />
      <PersonaStatusStrip chatId={chatId} initial={chat.data.persona_status} />
      <Timeline chatId={chatId} onExplain={setRunId} />
      <Composer chatId={chatId} />
      <ReplyRunSheet runId={runId} onClose={() => setRunId(null)} />
    </div>
  );
}
