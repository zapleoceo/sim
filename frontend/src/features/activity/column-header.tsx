import { ArrowDown, ArrowUp, ArrowUpDown, Filter } from 'lucide-react';
import type * as React from 'react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { TableHead } from '@/components/ui/table';
import type { TraceSortField } from '@/lib/trace-models';
import { cn } from '@/lib/utils';
import type { LogSort } from './log-logic';

/** Table header cell: the label sorts (click again to flip), the funnel opens this column's filter. */
export function ColumnHeader({
  field,
  label,
  sort,
  onSort,
  filtered = false,
  filter,
  className,
  align = 'left',
}: {
  field: TraceSortField;
  label: string;
  sort: LogSort;
  onSort: (field: TraceSortField) => void;
  filtered?: boolean;
  filter?: React.ReactNode;
  className?: string;
  align?: 'left' | 'right';
}) {
  const active = sort.field === field;
  const Icon = !active ? ArrowUpDown : sort.order === 'asc' ? ArrowUp : ArrowDown;
  return (
    <TableHead
      aria-sort={active ? (sort.order === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={cn('whitespace-nowrap', className)}
    >
      <div className={cn('flex items-center gap-0.5', align === 'right' && 'justify-end')}>
        <button
          type="button"
          onClick={() => onSort(field)}
          className={cn(
            'inline-flex items-center gap-1 rounded-sm px-1 py-0.5 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden',
            active && 'text-foreground',
          )}
          aria-label={`Сортировать: ${label}`}
        >
          {label}
          <Icon className={cn('size-3.5', !active && 'opacity-40')} aria-hidden />
        </button>
        {filter && (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label={`Фильтр: ${label}`}
                className={cn(
                  'relative inline-flex size-6 items-center justify-center rounded-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden',
                  filtered ? 'text-primary' : 'text-muted-foreground/60',
                )}
              >
                <Filter className="size-3.5" fill={filtered ? 'currentColor' : 'none'} aria-hidden />
              </button>
            </PopoverTrigger>
            <PopoverContent className="flex flex-col gap-2 text-sm font-normal" aria-label={`Фильтр: ${label}`}>
              <p className="text-xs font-medium text-muted-foreground">{label}</p>
              {filter}
            </PopoverContent>
          </Popover>
        )}
      </div>
    </TableHead>
  );
}

/** Multi-choice list for a header filter (kinds, statuses). */
export function CheckList<T extends string>({
  options,
  value,
  onToggle,
}: {
  options: { value: T; label: string }[];
  value: T[];
  onToggle: (value: T) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      {options.map((o) => (
        <label key={o.value} className="flex cursor-pointer items-center gap-2 rounded-sm px-1 py-1 hover:bg-accent">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={value.includes(o.value)}
            onChange={() => onToggle(o.value)}
          />
          {o.label}
        </label>
      ))}
    </div>
  );
}
