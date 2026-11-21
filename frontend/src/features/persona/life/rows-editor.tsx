import { Plus, Trash2 } from 'lucide-react';
import type * as React from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { AMBIENCE_OPTIONS, BUSY_OPTIONS, DAY_CODES, joinNames, splitNames, toggleDay } from './life-logic';

export type CellKind = 'text' | 'textarea' | 'time' | 'days' | 'busy' | 'ambience' | 'ambience-auto' | 'names';

export interface Column<T> {
  key: keyof T & string;
  label: string;
  kind: CellKind;
  /** Grid track on wide screens, e.g. `1fr` or `7rem`. */
  width: string;
}

function Cell<T>({ column, value, index, onChange }: {
  column: Column<T>;
  value: unknown;
  index: number;
  onChange: (value: unknown) => void;
}) {
  const label = `${column.label}, строка ${index + 1}`;
  switch (column.kind) {
    case 'textarea':
      return <Textarea aria-label={label} rows={2} className="min-h-9" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />;
    case 'time':
      return <Input aria-label={label} type="time" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />;
    case 'names': {
      // Free typing (commas included); the list is parsed on blur. The key re-creates the
      // input when the list changes from outside (a row above was deleted).
      const joined = joinNames((value as string[] | undefined) ?? []);
      return (
        <Input
          key={joined}
          aria-label={label}
          placeholder="через запятую"
          defaultValue={joined}
          onBlur={(e) => onChange(splitNames(e.target.value))}
        />
      );
    }
    case 'busy':
      return (
        <NativeSelect aria-label={label} value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {BUSY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </NativeSelect>
      );
    case 'ambience':
    case 'ambience-auto':
      return (
        <NativeSelect aria-label={label} value={String(value ?? '')} onChange={(e) => onChange(e.target.value || null)}>
          {column.kind === 'ambience-auto' && <option value="">по месту</option>}
          {AMBIENCE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </NativeSelect>
      );
    case 'days': {
      const days = (value as number[] | undefined) ?? [];
      return (
        <div role="group" aria-label={label} className="flex flex-wrap gap-1">
          {DAY_CODES.map((code, day) => (
            <button
              key={code}
              type="button"
              aria-pressed={days.includes(day)}
              onClick={() => onChange(toggleDay(days, day))}
              className={cn(
                'h-7 min-w-8 rounded-md border px-1.5 text-xs',
                days.includes(day) ? 'border-primary bg-primary text-primary-foreground' : 'bg-background text-muted-foreground',
              )}
            >
              {code}
            </button>
          ))}
        </div>
      );
    }
    default:
      return <Input aria-label={label} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />;
  }
}

/**
 * An editable table: a grid with a header row from 768 px, stacked cards with labels on a
 * phone. Every change returns the whole new list; saving is the caller's job.
 */
export function RowsEditor<T extends object>({ title, rows, columns, onChange, newRow, addLabel, emptyText }: {
  title: string;
  rows: readonly T[];
  columns: readonly Column<T>[];
  onChange: (rows: T[]) => void;
  newRow: () => T;
  addLabel: string;
  emptyText: string;
}) {
  const template = { '--cols': `${columns.map((c) => c.width).join(' ')} 2.25rem` } as React.CSSProperties;
  const update = (index: number, key: string, value: unknown) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  return (
    <div className="flex flex-col gap-2" aria-label={title} role="group">
      {rows.length > 0 && (
        <div style={template} className="hidden gap-2 px-1 text-xs font-medium text-muted-foreground md:grid md:[grid-template-columns:var(--cols)]">
          {columns.map((c) => <span key={c.key}>{c.label}</span>)}
          <span />
        </div>
      )}
      {rows.length === 0 && <p className="text-sm text-muted-foreground">{emptyText}</p>}
      {rows.map((row, index) => (
        <div
          // Rows have no ids; the index is their identity while editing.
          key={index}
          style={template}
          className="grid gap-2 rounded-lg border p-2 md:items-start md:rounded-none md:border-0 md:border-b md:p-1 md:[grid-template-columns:var(--cols)]"
        >
          {columns.map((column) => (
            <div key={column.key} className="flex min-w-0 flex-col gap-1">
              <span aria-hidden="true" className="text-xs text-muted-foreground md:hidden">{column.label}</span>
              <Cell
                column={column}
                index={index}
                value={(row as Record<string, unknown>)[column.key]}
                onChange={(value) => update(index, column.key, value)}
              />
            </div>
          ))}
          <Button
            variant="ghost-destructive"
            size="icon-sm"
            className="justify-self-end"
            aria-label={`Удалить строку ${index + 1}`}
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" className="w-fit" onClick={() => onChange([...rows, newRow()])}>
        <Plus />
        {addLabel}
      </Button>
    </div>
  );
}
