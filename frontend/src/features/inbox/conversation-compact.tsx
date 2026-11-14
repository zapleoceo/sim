import { Link } from '@tanstack/react-router';
import { ArrowLeft, CheckCheck, ChevronRight, Eraser, Milestone, MoreVertical, Rows3, User, X, Zap } from 'lucide-react';
import { useRef, useState } from 'react';
import { Avatar } from '@/components/avatar';
import { ChannelIcon, NeedsOperatorBadge, StageChip, StatusBadge } from '@/components/badges';
import { StageDropdown, type ApplyChoice } from '@/components/choice-dropdown';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import type { Client, Dialog, Stage } from '@/lib/models';
import { stageMeta } from '@/lib/stages';

/**
 * Phone header of a dialog — one row: back, name (truncated), the «ИИ» switch and «⋮».
 * Everything else the desktop header shows inline lives in the «⋮» menu: status badges, the stage,
 * «Обработано», the client feed and card, «Очистить чат». The handlers are the desktop ones.
 *
 * The menu shows the stage as its current value («Этап: Вовлечён»); choosing that item closes the
 * menu and opens the same stage dropdown the desktop header has, anchored under the header.
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
  onChangeStage?: ApplyChoice<Stage>;
  onClearChat?: () => void;
}) {
  const clientId = String(client.id);
  const [stageOpen, setStageOpen] = useState(false);
  // The menu must not take the focus back when it closes to open the stage list: the list would
  // see the focus leave and close at once.
  const openingStage = useRef(false);
  return (
    <header className="relative flex h-11 flex-none items-center gap-1.5 border-b px-1.5">
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
        <DropdownMenuContent align="end" className="w-64 max-w-[calc(100vw-1rem)]"
          onCloseAutoFocus={(e) => {
            if (openingStage.current) e.preventDefault();
            openingStage.current = false;
          }}>
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
              <DropdownMenuItem
                disabled={!stageMeta(client.stage).aiReachable}
                onSelect={() => {
                  openingStage.current = true;
                  setStageOpen(true);
                }}
              >
                <Milestone />
                <span className="min-w-0 flex-1 truncate">Этап: {stageMeta(client.stage).label}</span>
                <ChevronRight />
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {onChangeStage && (
        <StageDropdown anchor stage={client.stage} onApply={onChangeStage} open={stageOpen} onOpenChange={setStageOpen}
          className="absolute right-2 bottom-0" />
      )}
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
