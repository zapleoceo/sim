import { Search, SlidersHorizontal, X } from 'lucide-react';
import type * as React from 'react';
import { useIsPhone } from '@/hooks/use-media-query';
import { cn } from '@/lib/utils';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from './ui/sheet';

/** Search box with an icon; the «search» slot of FilterBar. */
export function SearchInput({
  value,
  onChange,
  placeholder = 'Поиск',
  className,
  maxLength,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  maxLength?: number;
}) {
  return (
    <div className={cn('relative min-w-0 flex-1', className)}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        value={value}
        maxLength={maxLength}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="pl-8"
      />
    </div>
  );
}

/**
 * Search + filters. Desktop: filters inline. Phone (or `alwaysSheet`): a «Фильтры» button with the active
 * count opens a bottom sheet. Reset appears when something is active.
 */
export function FilterBar({
  search,
  filters,
  activeCount = 0,
  onReset,
  alwaysSheet = false,
  className,
}: {
  search?: React.ReactNode;
  filters?: React.ReactNode;
  activeCount?: number;
  onReset?: () => void;
  alwaysSheet?: boolean;
  className?: string;
}) {
  const isPhone = useIsPhone();
  const inSheet = alwaysSheet || isPhone;
  const reset = onReset && activeCount > 0 && (
    <Button variant="ghost" size="sm" onClick={onReset}>
      <X />
      Сбросить
    </Button>
  );
  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      {search}
      {filters &&
        (inSheet ? (
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="outline" className="flex-none" aria-label="Фильтры">
                <SlidersHorizontal />
                {!alwaysSheet || !search ? 'Фильтры' : null}
                {activeCount > 0 && (
                  <Badge className="h-5 min-w-5 rounded-full px-1.5 tabular">{activeCount}</Badge>
                )}
              </Button>
            </SheetTrigger>
            <SheetContent side={isPhone ? 'bottom' : 'right'} className="overflow-y-auto">
              <SheetHeader>
                <SheetTitle>Фильтры</SheetTitle>
                <SheetDescription className="sr-only">Уточните выборку</SheetDescription>
              </SheetHeader>
              <div className="flex flex-col gap-3 px-4 pb-4 [&>*]:w-full">{filters}</div>
              {reset && <div className="px-4 pb-4">{reset}</div>}
            </SheetContent>
          </Sheet>
        ) : (
          <div className="flex flex-wrap items-center gap-2 [&>*]:w-auto [&>*]:min-w-40">{filters}</div>
        ))}
      {!inSheet && reset}
    </div>
  );
}

/** Labelled control inside a FilterBar (label is visible in the sheet, sr-only inline). */
export function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}
