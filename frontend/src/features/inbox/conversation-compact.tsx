import { Link } from '@tanstack/react-router';
import { ArrowLeft, CheckCheck, Eraser, MoreVertical, Rows3, User, X, Zap } from 'lucide-react';
import { Avatar } from '@/components/avatar';
import { ChannelIcon, NeedsOperatorBadge, StageChip, StatusBadge } from '@/components/badges';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import type { Client, Dialog, Stage } from '@/lib/models';
import { STAGES } from '@/lib/stages';

/**
 * Phone header of a dialog — one row: back, name (truncated), the «ИИ» switch and «⋮».
 * Everything else the desktop header shows inline lives in the «⋮» menu: status badges, the stage,
 * «Обработано», the client feed and card, «Очистить чат». The handlers are the desktop ones.
 */
export function CompactConversationHeader({
  dialog,
  client,
  clearing,
  onBack,
  onToggleAi,
  onSetUrgent,
  onResolve,
  onChangeStage,
  onClearChat,
}: {
  dialog: Dialog;
  client: Client;
  clearing: boolean;
  onBack: () => void;
  onToggleAi: (paused: boolean) => void;
  onSetUrgent?: (urgent: boolean) => void;
  onResolve: () => void;
  onChangeStage?: (stage: Stage) => void;
  onClearChat?: () => void;
}) {
  const clientId = String(client.id);
  return (
    <header className="flex h-11 flex-none items-center gap-1.5 border-b px-1.5">
      <Button variant="ghost" size="icon-sm" aria-label="Назад к списку" onClick={onBack}>
        <ArrowLeft />
      </Button>
      <Avatar name={client.display_name} url={client.avatar_url} size={28} />
      <strong className="min-w-0 flex-1 truncate text-sm font-semibold">{client.display_name}</strong>
      <div className="flex flex-none items-center gap-1.5">
        <Switch id="dialog-ai" checked={!dialog.ai_paused} onCheckedChange={(on) => onToggleAi(!on)} />
        <Label htmlFor="dialog-ai" className="text-xs">ИИ</Label>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Действия с диалогом" className="relative">
            <MoreVertical />
            {dialog.needs_operator && (
              <span aria-hidden className="absolute top-1 right-1 size-2 rounded-full bg-destructive" />
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64 max-w-[calc(100vw-1rem)]">
          <DropdownMenuLabel className="flex flex-wrap items-center gap-1.5 font-normal">
            {dialog.channel_kind && <ChannelIcon kind={dialog.channel_kind} size={18} />}
            <StageChip stage={client.stage} className="h-5 px-2 text-[11px]" />
            <StatusBadge status={client.status} />
            {dialog.needs_operator && <NeedsOperatorBadge />}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {dialog.needs_operator && (
            <DropdownMenuItem onSelect={onResolve}>
              <CheckCheck />
              Обработано
            </DropdownMenuItem>
          )}
          {onSetUrgent && (
            <DropdownMenuItem onSelect={() => onSetUrgent(!dialog.urgent)}>
              <Zap />
              {dialog.urgent ? 'Снять «срочный»' : 'Срочный: отвечать сразу'}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem asChild>
            <Link to="/clients/$id" params={{ id: clientId }} hash="conversation">
              <Rows3 />
              Лента клиента — все каналы
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link to="/clients/$id" params={{ id: clientId }}>
              <User />
              Карточка клиента
            </Link>
          </DropdownMenuItem>
          {onClearChat && (
            <DropdownMenuItem variant="destructive" disabled={clearing} onSelect={onClearChat}>
              <Eraser />
              Очистить чат
            </DropdownMenuItem>
          )}
          {onChangeStage && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Этап</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={client.stage} onValueChange={(stage) => onChangeStage(stage as Stage)}>
                {STAGES.filter((item) => item.aiReachable || item.id === client.stage).map((item) => (
                  <DropdownMenuRadioItem key={item.id} value={item.id}>
                    {item.label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}

/** Phone banner «нужен оператор»: one or two short lines, hidden with «×» (the «⋮» dot and menu badge stay). */
export function CompactOperatorBanner({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div role="alert" className="flex flex-none items-center gap-2 border-b bg-warning/15 py-1 pr-1 pl-3 text-xs leading-snug font-medium">
      <span className="min-w-0 flex-1">Требуется ответ менеджера. ИИ остановлен.</span>
      <Button variant="ghost" size="icon-sm" className="size-7" aria-label="Скрыть уведомление" onClick={onDismiss}>
        <X />
      </Button>
    </div>
  );
}
