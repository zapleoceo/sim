import type { ColumnDef } from '@tanstack/react-table';
import { Merge, X } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import type { Lead } from '@/lib/types';
import { clientName } from '@/lib/utils';
import { useMergeClients } from './client-matches';
import { mergeBlocker, olderFirst } from './merge-logic';

/** The leads ticked in the table, in tick order (a lead that left the list drops out). */
export function useLeadSelection(rows: readonly Lead[]) {
  const [ids, setIds] = useState<number[]>([]);
  const toggle = (id: number): void => setIds((now) => (now.includes(id) ? now.filter((x) => x !== id) : [...now, id]));
  const chosen = ids.map((id) => rows.find((row) => row.id === id)).filter((row): row is Lead => row !== undefined);
  return { chosen, toggle, clear: () => setIds([]) };
}

/** The tick column of the leads table. */
export function selectColumn(chosen: readonly Lead[], toggle: (id: number) => void): ColumnDef<Lead, unknown> {
  return {
    id: 'select',
    header: () => <span className="sr-only">Выбор</span>,
    cell: ({ row }) => (
      <input
        type="checkbox"
        className="size-4 cursor-pointer accent-primary"
        aria-label={`Выбрать ${clientName(row.original)}`}
        checked={chosen.some((lead) => lead.id === row.original.id)}
        onChange={() => toggle(row.original.id)}
      />
    ),
    meta: { className: 'w-10 pl-3' },
  };
}

/**
 * Over the table while leads are ticked: two of them can be merged by hand (the older one stays,
 * the same confirmation as a suggested pair); otherwise it says what is missing.
 */
export function SelectionBar({ chosen, clear }: { chosen: readonly Lead[]; clear: () => void }) {
  const { merge, pending } = useMergeClients();
  if (chosen.length === 0) return null;
  const pair = chosen.length === 2 ? olderFirst(chosen[0], chosen[1]) : null;
  const blocker = pair ? mergeBlocker(pair[0], pair[1]) : null;
  let hint = 'Отметьте второго лида, чтобы объединить их.';
  if (chosen.length > 2) hint = 'Объединяются двое: оставьте отмеченными двух лидов.';
  if (pair) hint = blocker ?? `Останется «${clientName(pair[0])}» (старший), «${clientName(pair[1])}» перейдёт в него.`;
  return (
    <div role="region" aria-label="Выбранные лиды" className="flex flex-wrap items-center gap-3 rounded-xl border bg-accent/40 px-4 py-2.5 text-sm">
      <span className="font-medium">Выбрано: {chosen.length}</span>
      <span className={blocker ? 'text-warning' : 'text-muted-foreground'}>{hint}</span>
      <span className="ml-auto flex gap-2">
        <Button
          size="sm"
          disabled={!pair || blocker !== null || pending}
          onClick={() => {
            if (pair) void merge(pair[0], pair[1]).then((done) => done && clear());
          }}
        >
          <Merge />
          Объединить
        </Button>
        <Button size="sm" variant="ghost" onClick={clear}>
          <X />
          Снять выбор
        </Button>
      </span>
    </div>
  );
}
