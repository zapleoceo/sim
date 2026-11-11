import { AlertCircle, type LucideIcon } from 'lucide-react';
import type * as React from 'react';
import { cn } from '@/lib/utils';
import { Button } from './ui/button';

export function EmptyState({
  icon: Icon,
  title,
  hint,
  children,
  className,
}: {
  icon?: LucideIcon;
  title: React.ReactNode;
  hint?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 px-4 py-10 text-center', className)}>
      {Icon && (
        <span className="mb-1 flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Icon className="size-5" />
        </span>
      )}
      <p className="font-medium">{title}</p>
      {hint && <p className="max-w-sm text-sm text-muted-foreground">{hint}</p>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}

/** Load error with a retry button; announced to screen readers. */
export function ErrorState({ title = 'Не удалось загрузить данные', onRetry }: { title?: string; onRetry?: () => void }) {
  return (
    <div role="alert">
      <EmptyState icon={AlertCircle} title={title} hint="Проверьте соединение и повторите попытку.">
        {onRetry && (
          <Button variant="outline" onClick={onRetry}>
            Повторить
          </Button>
        )}
      </EmptyState>
    </div>
  );
}

/** Inline error under a form or block. */
export function InlineError({ children, className }: { children: React.ReactNode; className?: string }) {
  if (!children) return null;
  return (
    <p role="alert" className={cn('flex items-start gap-1.5 text-sm text-destructive', className)}>
      <AlertCircle className="mt-0.5 size-4 flex-none" />
      <span>{children}</span>
    </p>
  );
}
