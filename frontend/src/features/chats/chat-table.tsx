import { Link, useNavigate } from '@tanstack/react-router';
import { ChannelBadge, channelLook } from '@/components/channel-icon';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatDateTime, formatShort } from '@/lib/format';
import type { ChatRow } from '@/lib/types';
import type { ChatsSearch } from '@/router';
import { clientName, cn } from '@/lib/utils';
import { AiState, ChatAvatar, OperatorBadge, UnreadBadge } from './chat-row-parts';
import { PersonaStatusPill } from './persona-status';
import { lastMessagePreview } from './timeline-logic';

/**
 * The wide chat list (no chat open, from 1024 px): one row per chat across the whole width —
 * messenger, client, the last message, «Статус Марины», the persona (with more than one), AI and
 * time with the unread counter under it. A row with unread messages has an accent bar on the
 * left. A click anywhere on the row opens the chat; the client's name is the keyboard link.
 */
export function ChatTable({ items, showPersona, filters }: {
  items: ChatRow[];
  showPersona: boolean;
  /** The list's filters: opening a chat keeps them in the address. */
  filters: Omit<ChatsSearch, 'id'>;
}) {
  const navigate = useNavigate();
  const open = (id: number): void => void navigate({ to: '/chats', search: { ...filters, id } });
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
          const unread = chat.unread_count > 0;
          return (
            <TableRow
              key={chat.id}
              data-unread={unread || undefined}
              className="h-16 cursor-pointer data-[unread]:bg-primary/[0.03]"
              onClick={() => open(chat.id)}
            >
              <TableCell className={cn('pl-4', unread && 'shadow-[inset_3px_0_0_var(--primary)]')}>
                <span className="inline-flex items-center gap-2 whitespace-nowrap">
                  <ChannelBadge kind={chat.channel?.kind} />
                  {channelLook(chat.channel?.kind).label}
                </span>
              </TableCell>
              <TableCell className="max-w-64">
                <span className="flex min-w-0 items-center gap-2.5">
                  <ChatAvatar chat={chat} size={34} messenger={false} />
                  <span className="flex min-w-0 flex-col">
                    <Link
                      to="/chats"
                      search={{ ...filters, id: chat.id }}
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
                  {chat.needs_operator && <OperatorBadge />}
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
                  <UnreadBadge count={chat.unread_count} />
                </span>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
