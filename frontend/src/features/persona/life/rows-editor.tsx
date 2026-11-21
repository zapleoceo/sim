import { Plus, Trash2 } from 'lucide-react';
import type * as React from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { AMBIENCE_OPTIONS, BUSY_OPTIONS, DAY_CODES, joinNames, splitNames, toggleDay } from './life-logic';

export type CellKind = 'text' | 'textarea' | 'time' | 'days' | 'busy' | 'ambience' | 'names';

export interface Column<T> {
  key: keyof T & string;
  label: string;
  kind: CellKind;
  /** Grid track of the table layout: `7rem` or `minmax(7rem,1fr)` (its minimum is in rem). */
  width: string;
}

/** The delete button's track, the gap between tracks and the row padding (rem), as in the classes below. */
const DELETE_REM = 2.25;
const GAP_REM = 0.5;
const ROW_PADDING_REM = 0.5;

/** The narrowest width (rem) at which every column of the table fits its minimum. */
export function gridMinRem(widths: readonly string[]): number {
  const tracks = widths.map((w) => Number(/^(?:minmax\()?([\d.]+)rem/.exec(w)?.[1] ?? 0));
  return tracks.reduce((sum, rem) => sum + rem, DELETE_REM) + GAP_REM * widths.length + ROW_PADDING_REM;
}

/**
 * The container widths (rem) at which a table becomes a grid with a header row; below its step
 * the rows are cards. Literal classes so Tailwind generates them: each sets the variables the
 * rows read (`--rows-cols`, `--rows-head`, `--rows-label`).
 */
const GRID_STEPS: readonly (readonly [number, string])[] = [
  [32, '@min-[32rem]:[--rows-cols:var(--cols)] @min-[32rem]:[--rows-head:grid] @min-[32rem]:[--rows-label:none]'],
  [40, '@min-[40rem]:[--rows-cols:var(--cols)] @min-[40rem]:[--rows-head:grid] @min-[40rem]:[--rows-label:none]'],
  [48, '@min-[48rem]:[--rows-cols:var(--cols)] @min-[48rem]:[--rows-head:grid] @min-[48rem]:[--rows-label:none]'],
  [56, '@min-[56rem]:[--rows-cols:var(--cols)] @min-[56rem]:[--rows-head:grid] @min-[56rem]:[--rows-label:none]'],
  [62, '@min-[62rem]:[--rows-cols:var(--cols)] @min-[62rem]:[--rows-head:grid] @min-[62rem]:[--rows-label:none]'],
  [68, '@min-[68rem]:[--rows-cols:var(--cols)] @min-[68rem]:[--rows-head:grid] @min-[68rem]:[--rows-label:none]'],
  [72, '@min-[72rem]:[--rows-cols:var(--cols)] @min-[72rem]:[--rows-head:grid] @min-[72rem]:[--rows-label:none]'],
  [76, '@min-[76rem]:[--rows-cols:var(--cols)] @min-[76rem]:[--rows-head:grid] @min-[76rem]:[--rows-label:none]'],
  [84, '@min-[84rem]:[--rows-cols:var(--cols)] @min-[84rem]:[--rows-head:grid] @min-[84rem]:[--rows-label:none]'],
  [96, '@min-[96rem]:[--rows-cols:var(--cols)] @min-[96rem]:[--rows-head:grid] @min-[96rem]:[--rows-label:none]'],
];

/** The smallest step that fits the table (rem) and its classes; wider tables use the last one. */
export function gridStep(minRem: number): readonly [number, string] {
  return GRID_STEPS.find(([rem]) => rem >= minRem) ?? GRID_STEPS[GRID_STEPS.length - 1]!;
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
      return (
        <NativeSelect aria-label={label} value={String(value ?? '')} onChange={(e) => onChange(e.target.value || null)}>
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
 * An editable table. A container query decides the layout by the width the table actually has
 * (not the screen): wide enough for every column's minimum — a grid with a header row; narrower
 * — cards with labelled fields that flow in as many columns as fit. Never a horizontal scroll.
 * Every change returns the whole new list; saving is the caller's job.
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
  const widths = columns.map((c) => c.width);
  const [, gridClasses] = gridStep(gridMinRem(widths));
  const template = { '--cols': `${widths.join(' ')} ${DELETE_REM}rem` } as React.CSSProperties;
  const update = (index: number, key: string, value: unknown) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  return (
    <div className="@container min-w-0">
      <div style={template} className={cn('flex flex-col gap-2', gridClasses)} aria-label={title} role="group">
        {rows.length > 0 && (
          <div className="gap-2 px-1 text-xs font-medium text-muted-foreground [display:var(--rows-head,none)] [grid-template-columns:var(--cols)]">
            {columns.map((c) => <span key={c.key}>{c.label}</span>)}
            <span />
          </div>
        )}
        {rows.length === 0 && <p className="text-sm text-muted-foreground">{emptyText}</p>}
        {rows.map((row, index) => (
          <div
            // Rows have no ids; the index is their identity while editing.
            key={index}
            className="grid items-start gap-2 rounded-lg border p-1 [grid-template-columns:var(--rows-cols,repeat(auto-fill,minmax(11rem,1fr)))]"
          >
            {columns.map((column) => (
              <div key={column.key} className="flex min-w-0 flex-col gap-1">
                <span aria-hidden="true" className="text-xs text-muted-foreground [display:var(--rows-label,block)]">{column.label}</span>
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
    </div>
  );
}
