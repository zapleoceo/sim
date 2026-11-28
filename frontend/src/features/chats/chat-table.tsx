import { Link, useNavigate } from '@tanstack/react-router';
import { channelLook } from '@/components/channel-icon';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatDateTime, formatShort } from '@/lib/format';
import type { ChatRow } from '@/lib/types';
import type { ChatsSearch } from '@/router';
import { clientName, cn } from '@/lib/utils';
import { ChatAvatar, OperatorBadge, UnreadBadge } from './chat-row-parts';
import { PersonaStatusPill } from './persona-status';
import { aiActive, lastMessagePreview } from './timeline-logic';

function Messenger({ kind }: { kind: string | null | undefined }) {
  const { label, icon: Icon } = channelLook(kind);
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <span className="flex size-7 flex-none items-center justify-center rounded-full bg-muted">
        <Icon aria-hidden="true" className="size-3.5" />
      </span>
      {label}
    </span>
  );
}

/**
 * The wide chat list (no chat open, from 1024 px): one row per chat across the whole width —
 * messenger, client, the last message, «Статус Марины», the persona (with more than one), AI and
 * time. A click anywhere on the row opens the chat; the client's name is the keyboard link.
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
          <TableHead className="w-72">Статус</TableHead>
          {showPersona && <TableHead className="hidden w-32 xl:table-cell">Персонаж</TableHead>}
          <TableHead className="w-40">ИИ</TableHead>
          <TableHead className="w-24 pr-4 text-right">Время</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((chat) => {
          const name = clientName(chat.client);
          const unread = chat.unread_count > 0;
          return (
            <TableRow key={chat.id} className="cursor-pointer" onClick={() => open(chat.id)}>
              <TableCell className="pl-4">
                <Messenger kind={chat.channel?.kind} />
              </TableCell>
              <TableCell className="max-w-64">
                <span className="flex min-w-0 items-center gap-2.5">
                  <ChatAvatar chat={chat} size={34} />
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
                  <UnreadBadge count={chat.unread_count} />
                </span>
              </TableCell>
              <TableCell className="w-full max-w-0">
                <span className={cn('block truncate', unread ? 'text-foreground' : 'text-muted-foreground')}>
                  {lastMessagePreview(chat.last_message)}
                </span>
              </TableCell>
              <TableCell className="max-w-72">
                <PersonaStatusPill status={chat.persona_status} />
              </TableCell>
              {showPersona && <TableCell className="hidden truncate xl:table-cell">{chat.persona?.name ?? '—'}</TableCell>}
              <TableCell>
                <span className="flex flex-wrap items-center gap-1.5">
                  {aiActive(chat) ? <Badge variant="success">отвечает</Badge> : <Badge variant="muted">выключен</Badge>}
                  {chat.needs_operator && <OperatorBadge />}
                </span>
              </TableCell>
              <TableCell className="pr-4 text-right whitespace-nowrap text-muted-foreground tabular">
                <time dateTime={chat.last_message_at ?? undefined} title={formatDateTime(chat.last_message_at)}>
                  {formatShort(chat.last_message_at) || '—'}
                </time>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
