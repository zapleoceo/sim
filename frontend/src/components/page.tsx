import { Link, type LinkProps } from '@tanstack/react-router';
import { ArrowLeft } from 'lucide-react';
import type * as React from 'react';
import { cn } from '@/lib/utils';

const WIDTHS = { narrow: 'max-w-3xl', default: 'max-w-6xl', wide: 'max-w-none' } as const;

/** Page container: consistent paddings and max width across screens. */
export function Page({
  width = 'default',
  className,
  children,
}: {
  width?: keyof typeof WIDTHS;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn('mx-auto flex w-full flex-col gap-4 px-3 py-3 sm:gap-5 sm:px-6 sm:py-6', WIDTHS[width], className)}>
      {children}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  children,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold sm:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </header>
  );
}

/** «← Все …» link above editor pages. */
export function BackLink({ children, ...props }: LinkProps & { children: React.ReactNode }) {
  return (
    <Link
      {...props}
      className="-ml-1 inline-flex w-fit items-center gap-1.5 rounded-md px-1 py-0.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="size-4" />
      {children}
    </Link>
  );
}
