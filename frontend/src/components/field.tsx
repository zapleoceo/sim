import type * as React from 'react';
import { cn } from '@/lib/utils';
import { Label } from './ui/label';

/**
 * Label + control + hint/error, one layout for every form field. `labelAction` (a button next to
 * the label, e.g. the passport's lock) stays outside the `<label>`, so the control's name is the
 * label text alone.
 */
export function Field({
  id,
  label,
  labelAction,
  hint,
  error,
  className,
  children,
}: {
  id: string;
  label: React.ReactNode;
  labelAction?: React.ReactNode;
  hint?: React.ReactNode;
  error?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      {labelAction ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <Label htmlFor={id}>{label}</Label>
          {labelAction}
        </div>
      ) : (
        <Label htmlFor={id}>{label}</Label>
      )}
      {children}
      {error ? (
        <p role="alert" className="text-xs text-destructive">{error}</p>
      ) : (
        hint && <p className="text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}

/** A switch row: title and hint on the left, the control on the right. */
export function SwitchRow({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: React.ReactNode;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="flex min-w-0 flex-col gap-0.5">
        <Label htmlFor={id}>{label}</Label>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  );
}

/** Titled section of a long settings page, with an anchor id for in-page links. */
export function Section({
  id,
  title,
  description,
  actions,
  children,
}: {
  id: string;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className="flex scroll-mt-4 flex-col gap-4 rounded-xl border bg-card p-4 shadow-xs sm:p-5"
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id={`${id}-title`} className="text-base font-semibold">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}
