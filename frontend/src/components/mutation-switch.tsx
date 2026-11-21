import type * as React from 'react';
import { cn } from '@/lib/utils';
import { Switch } from './ui/switch';

/** While the change is being saved the switch shows the new value; otherwise the saved one. */
export function shownValue(saved: boolean, pending: boolean, pendingValue: boolean | undefined): boolean {
  return pending && pendingValue !== undefined ? pendingValue : saved;
}

/**
 * A switch that saves right away (AI in a chat or for a client, the chat flags, «референс»):
 * shows the value being saved and is disabled meanwhile. With `label` it is a labelled row.
 */
export function MutationSwitch({ checked, pending, pendingValue, onCheckedChange, label, title, className, ...aria }: {
  /** The saved value. */
  checked: boolean;
  pending: boolean;
  /** The value being saved (the mutation's variables). */
  pendingValue?: boolean;
  onCheckedChange: (value: boolean) => void;
  label?: React.ReactNode;
  title?: string;
  className?: string;
  'aria-label'?: string;
}) {
  const control = (
    <Switch
      checked={shownValue(checked, pending, pendingValue)}
      disabled={pending}
      onCheckedChange={onCheckedChange}
      aria-label={aria['aria-label']}
    />
  );
  if (!label) return control;
  return (
    <label className={cn('flex items-center gap-2 text-sm', className)} title={title}>
      {control}
      {label}
    </label>
  );
}
