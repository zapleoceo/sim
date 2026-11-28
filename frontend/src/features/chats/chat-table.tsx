import { Link, useNavigate } from '@tanstack/react-router';
import { channelLook } from '@/components/channel-icon';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatDateTime, formatShort } from '@/lib/format';
import type { ChatRow } from '@/lib/types';
import type { ChatsSearch } from '@/router';
import { clientName, cn } from '@/lib/utils';
import { AiState, ChatAvatar, MessengerBadges, OperatorBadge, UnreadBadge } from './chat-row-parts';
import { PersonaStatusPill } from './persona-status';
import { needsOperator, personMessengers, replyChatId, unreadTotal } from './person-logic';
import { lastMessagePreview } from './timeline-logic';

/**
 * The wide chat list (no chat open, from 1024 px): one row per person (a client with a persona)
 * across the whole width — their messengers, client, the last message, «Статус Марины», the persona (with more than one), AI and
 * time with the unread counter under it. A row with unread messages has an accent bar on the
 * left. A click anywhere on the row opens the person's feed on the chat the client wrote in last;
 * the client's name is the keyboard link.
 */
export function ChatTable({ items, showPersona, filters }: {
  items: ChatRow[];
  showPersona: boolean;
  /** The list's filters: opening a chat keeps them in the address. */
  filters: Omit<ChatsSearch, 'id'>;
}) {
  const navigate = useNavigate();
  const open = (id: number): void => void navigate({ to: '/chats', search: { ...filters, id } });
  const messengersOf = (chat: ChatRow): string => personMessengers(chat).map((k) => channelLook(k).label).join(' · ');
  return (
    <Table aria-label="Чаты">
      <TableHeader className="bg-muted/40">
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-36 pl-4">Мессенджер</TableHead>
          <TableHead className="w-64">Собеседник</TableHead>
          <TableHead>Последнее сообщение</TableHead>
          <TableHead className="w-80">Статус</TableHead>
          {showPersona && <TableHead className="hidden w-32 xl:table-cell">Персонаж</TableHead>}
          <TableHead className="w-36">ИИ</TableHead>
          <TableHead className="w-24 pr-4 text-right">Время</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((chat) => {
          const name = clientName(chat.client);
          const unreadCount = unreadTotal(chat);
          const unread = unreadCount > 0;
          const target = replyChatId(chat);
          return (
            <TableRow
              key={chat.id}
              data-unread={unread || undefined}
              className="h-16 cursor-pointer data-[unread]:bg-primary/[0.03]"
              onClick={() => open(target)}
            >
              <TableCell className={cn('pl-4', unread && 'shadow-[inset_3px_0_0_var(--primary)]')}>
                <span className="inline-flex items-center gap-2">
                  <MessengerBadges kinds={personMessengers(chat)} />
                  <span className="max-w-28 text-xs leading-tight">{messengersOf(chat)}</span>
                </span>
              </TableCell>
              <TableCell className="max-w-64">
                <span className="flex min-w-0 items-center gap-2.5">
                  <ChatAvatar chat={chat} size={34} messenger={false} />
                  <span className="flex min-w-0 flex-col">
                    <Link
                      to="/chats"
                      search={{ ...filters, id: target }}
                      onClick={(e) => e.stopPropagation()}
                      className={cn('truncate hover:underline', unread ? 'font-semibold' : 'font-medium')}
                    >
                      {name}
                    </Link>
                    {chat.client.username && (
                      <span className="truncate text-xs text-muted-foreground">@{chat.client.username}</span>
                    )}
                  </span>
                </span>
              </TableCell>
              <TableCell className="w-full max-w-0">
                <span className={cn('block truncate', unread ? 'text-foreground' : 'text-muted-foreground')}>
                  {lastMessagePreview(chat.last_message)}
                </span>
              </TableCell>
              <TableCell className="max-w-80">
                <PersonaStatusPill status={chat.persona_status} />
              </TableCell>
              {showPersona && <TableCell className="hidden truncate xl:table-cell">{chat.persona?.name ?? '—'}</TableCell>}
              <TableCell>
                <span className="flex flex-col items-start gap-1">
                  <AiState chat={chat} />
                  {needsOperator(chat) && <OperatorBadge />}
                </span>
              </TableCell>
              <TableCell className="pr-4 text-right">
                <span className="flex flex-col items-end gap-1">
                  <time
                    className={cn('text-xs whitespace-nowrap tabular', unread ? 'font-medium text-primary' : 'text-muted-foreground')}
                    dateTime={chat.last_message_at ?? undefined}
                    title={formatDateTime(chat.last_message_at)}
                  >
                    {formatShort(chat.last_message_at) || '—'}
                  </time>
                  <UnreadBadge count={unreadCount} />
                </span>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
