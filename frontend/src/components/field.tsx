import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Label } from './ui/label';

interface ControlProps {
  id?: string;
  'aria-invalid'?: boolean;
  'aria-describedby'?: string;
}

/**
 * Label + control + hint/error, wired for screen readers.
 * The single child control receives `id`, `aria-invalid` and `aria-describedby`.
 */
export function Field({
  label,
  hint,
  error,
  counter,
  className,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  counter?: ReactNode;
  className?: string;
  children: ReactElement<ControlProps>;
}) {
  const id = useId();
  const noteId = `${id}-note`;
  const hasNote = Boolean(error || hint);
  const control = isValidElement(children)
    ? cloneElement(children, {
        id: children.props.id ?? id,
        'aria-invalid': error ? true : undefined,
        'aria-describedby': hasNote ? noteId : undefined,
      })
    : children;
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <Label htmlFor={children.props.id ?? id}>{label}</Label>
      {control}
      {(hasNote || counter) && (
        <div className="flex items-start justify-between gap-3 text-xs">
          {error ? (
            <p id={noteId} role="alert" className="text-destructive">
              {error}
            </p>
          ) : hint ? (
            <p id={noteId} className="text-muted-foreground">
              {hint}
            </p>
          ) : (
            <span />
          )}
          {counter && <span className="flex-none text-muted-foreground tabular">{counter}</span>}
        </div>
      )}
    </div>
  );
}

/** Two-column responsive grid for form fields; `col-span-full` children take a full row. */
export function FormGrid({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('grid grid-cols-1 gap-4 sm:grid-cols-2', className)}>{children}</div>;
}
