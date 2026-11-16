import { Check, ChevronDown } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import type { ClientStatus, Stage } from '@/lib/models';
import { PARTNER_ONLY_HINT, STAGES, STATUSES, STATUS_LABELS, stageMeta } from '@/lib/stages';
import { cn } from '@/lib/utils';
import { StageChip } from './badges';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from './ui/popover';

export interface Choice<V extends string> { value: V; label: string; color?: string; disabled?: boolean }

/** Resolves `false` when the change was not accepted (the list then stays open). */
export type ApplyChoice<V extends string> = (value: V, reason: string) => Promise<boolean | void> | boolean | void;

export const REASON_MAX = 1000;

/** The open list: pick a value, optionally say why, apply. Mounted only while open, so it starts clean. */
function ChoiceList<V extends string>({ label, value, options, hint, onApply, onDone }: {
  label: string;
  value: V;
  options: readonly Choice<V>[];
  hint?: string;
  onApply: ApplyChoice<V>;
  onDone: () => void;
}) {
  const [picked, setPicked] = useState<V>(value);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const reasonId = useId();

  const apply = async (): Promise<void> => {
    if (picked === value || busy) return;
    setBusy(true);
    const accepted = await Promise.resolve(onApply(picked, reason.trim())).catch(() => false);
    if (accepted === false) setBusy(false);
    else onDone();
  };

  return (
    <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void apply(); }}>
      <div role="listbox" aria-label={label} className="flex flex-col">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="option"
            aria-selected={o.value === picked}
            disabled={o.disabled || busy}
            onClick={() => setPicked(o.value)}
            className={cn(
              'flex min-h-9 items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm outline-none hover:bg-accent focus-visible:bg-accent disabled:pointer-events-none disabled:opacity-50',
              o.value === picked && 'bg-accent',
            )}
          >
            {o.color && <span aria-hidden className="size-2 flex-none rounded-full" style={{ background: o.color }} />}
            <span className="min-w-0 flex-1 truncate">{o.label}</span>
            {o.value === value && <span className="text-[11px] text-muted-foreground">сейчас</span>}
            {o.value === picked && <Check aria-hidden className="size-4 flex-none" />}
          </button>
        ))}
      </div>
      {hint && <p className="px-2 text-xs text-muted-foreground">{hint}</p>}
      <div className="flex flex-col gap-1 border-t pt-2">
        <Label htmlFor={reasonId} className="text-xs font-normal text-muted-foreground">Причина (необязательно)</Label>
        <Input id={reasonId} value={reason} maxLength={REASON_MAX} placeholder="Почему меняете"
          onChange={(e) => setReason(e.target.value)} />
      </div>
      <Button type="submit" size="sm" disabled={picked === value || busy}>Применить</Button>
    </form>
  );
}

/**
 * One dropdown for a value that is changed on purpose (the stage, the status): the trigger shows
 * the current value, the list lets the operator pick another one, add an optional reason and
 * apply. The reason goes to the communication journal; empty means no reason (the journal line
 * then shows no details).
 *
 * `anchor` renders no trigger: the list is opened from elsewhere through `open` (the phone «⋮»
 * menu) and appears at the anchor.
 */
export function ChoiceDropdown<V extends string>({
  label, value, options, onApply, hint, disabled, className, children, open, onOpenChange, anchor = false,
}: {
  label: string;
  value: V;
  options: readonly Choice<V>[];
  onApply: ApplyChoice<V>;
  hint?: string;
  disabled?: boolean;
  className?: string;
  /** What the trigger shows; the current value's label by default. */
  children?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  anchor?: boolean;
}) {
  const [inner, setInner] = useState(false);
  const isOpen = open ?? inner;
  const setOpen = (next: boolean): void => {
    setInner(next);
    onOpenChange?.(next);
  };
  const current = options.find((o) => o.value === value)?.label ?? value;
  return (
    <Popover open={isOpen} onOpenChange={setOpen}>
      {anchor ? (
        <PopoverAnchor asChild>
          <span aria-hidden className={className} />
        </PopoverAnchor>
      ) : (
        <PopoverTrigger asChild>
          <button
            type="button"
            role="combobox"
            aria-label={label}
            disabled={disabled}
            className={cn(
              'inline-flex h-7 max-w-full min-w-0 items-center gap-1 rounded-full border border-input pr-1.5 text-xs outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60',
              className,
            )}
          >
            {children ?? <span className="min-w-0 truncate pl-2.5">{current}</span>}
            <ChevronDown aria-hidden className="size-3.5 flex-none opacity-60" />
          </button>
        </PopoverTrigger>
      )}
      <PopoverContent aria-label={label} align={anchor ? 'end' : 'start'} className="w-64">
        <ChoiceList label={label} value={value} options={options} hint={hint} onApply={onApply}
          onDone={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

const FIELD_TRIGGER = 'h-9 w-full justify-between rounded-md pr-3 text-sm shadow-xs [&>span]:pl-3';

type Controlled = { open?: boolean; onOpenChange?: (open: boolean) => void; anchor?: boolean };

const STAGE_CHOICES: readonly Choice<Stage>[] = STAGES.map((s) => ({
  value: s.id, label: s.label, color: s.color, disabled: !s.aiReachable,
}));
const STATUS_CHOICES: readonly Choice<ClientStatus>[] = STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }));

/**
 * The funnel stage of a client, the same list everywhere (dialog header, phone «⋮» menu, client
 * card). Registration and payment come only from partner data: they are shown but cannot be
 * picked, and a client already there cannot be moved by hand.
 */
export function StageDropdown({ stage, onApply, disabled, className, field = false, ...controlled }: {
  stage: Stage;
  onApply: ApplyChoice<Stage>;
  disabled?: boolean;
  className?: string;
  /** Looks like a form field (the client card) instead of a chip (the dialog header). */
  field?: boolean;
} & Controlled) {
  return (
    <ChoiceDropdown<Stage>
      label="Этап"
      value={stage}
      options={STAGE_CHOICES}
      onApply={onApply}
      hint={`Регистрация и оплата: ${PARTNER_ONLY_HINT}.`}
      disabled={disabled || !stageMeta(stage).aiReachable}
      className={cn(field && FIELD_TRIGGER, className)}
      {...controlled}
    >
      {field ? undefined : <StageChip stage={stage} className="h-[26px] px-2 text-[11px]" />}
    </ChoiceDropdown>
  );
}

/** The client's status, changed the same way as the stage. */
export function StatusDropdown({ status, onApply, disabled, className }: {
  status: ClientStatus;
  onApply: ApplyChoice<ClientStatus>;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <ChoiceDropdown<ClientStatus> label="Статус" value={status} options={STATUS_CHOICES} onApply={onApply}
      disabled={disabled} className={cn(FIELD_TRIGGER, className)} />
  );
}

