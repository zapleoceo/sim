import { Brain, Check, Pencil, Plus, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { useConfirm } from '@/components/confirm';
import { EmptyState } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { formatDateTime } from '@/lib/format';
import type { MemoryFact } from '@/lib/types';

/** Pure list operations; the whole list goes back in one PATCH `{memory}`. */
export const memoryOps = {
  add: (list: readonly MemoryFact[], fact: string, now = new Date()): MemoryFact[] => [
    ...list,
    { fact: fact.trim(), event_id: null, at: now.toISOString() },
  ],
  edit: (list: readonly MemoryFact[], index: number, fact: string): MemoryFact[] =>
    list.map((m, i) => (i === index ? { ...m, fact: fact.trim() } : m)),
  remove: (list: readonly MemoryFact[], index: number): MemoryFact[] => list.filter((_, i) => i !== index),
};

function FactRow({ fact, busy, onSave, onRemove }: {
  fact: MemoryFact;
  busy: boolean;
  onSave: (text: string) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(fact.fact);
  if (editing) {
    return (
      <li className="py-2">
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            onSave(text);
            setEditing(false);
          }}
        >
          <Input value={text} onChange={(e) => setText(e.target.value)} aria-label="Факт" autoFocus />
          <Button type="submit" variant="ghost" size="icon-sm" disabled={busy || !text.trim()} aria-label="Сохранить факт">
            <Check />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={() => setEditing(false)} aria-label="Отменить">
            <X />
          </Button>
        </form>
      </li>
    );
  }
  return (
    <li className="flex items-start gap-2 py-2">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="text-sm break-words">{fact.fact}</span>
        {fact.at && <span className="text-xs text-muted-foreground">{formatDateTime(fact.at)}</span>}
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => {
          setText(fact.fact);
          setEditing(true);
        }}
        aria-label={`Изменить: ${fact.fact}`}
      >
        <Pencil />
      </Button>
      <Button variant="ghost-destructive" size="icon-sm" onClick={onRemove} disabled={busy} aria-label={`Удалить: ${fact.fact}`}>
        <Trash2 />
      </Button>
    </li>
  );
}

/** What the agent remembers about the client (docs/context.md §4): add, edit, delete facts. */
export function MemoryEditor({ memory, busy, onChange }: {
  memory: readonly MemoryFact[];
  busy: boolean;
  onChange: (next: MemoryFact[], action: 'add' | 'edit' | 'remove') => void;
}) {
  const confirm = useConfirm();
  const [draft, setDraft] = useState('');
  const remove = async (index: number): Promise<void> => {
    const ok = await confirm({
      title: 'Удалить факт?',
      message: `«${memory[index]?.fact ?? ''}» — агент перестанет это помнить.`,
      confirmLabel: 'Удалить',
      danger: true,
    });
    if (ok) onChange(memoryOps.remove(memory, index), 'remove');
  };
  return (
    <div className="flex flex-col gap-3">
      {memory.length === 0 ? (
        <EmptyState icon={Brain} title="Пока ничего не запомнила" className="py-4" />
      ) : (
        <ul className="flex flex-col divide-y">
          {memory.map((m, i) => (
            <FactRow
              key={`${i}-${m.fact}`}
              fact={m}
              busy={busy}
              onSave={(text) => onChange(memoryOps.edit(memory, i, text), 'edit')}
              onRemove={() => void remove(i)}
            />
          ))}
        </ul>
      )}
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!draft.trim()) return;
          onChange(memoryOps.add(memory, draft), 'add');
          setDraft('');
        }}
      >
        <label className="min-w-0 flex-1">
          <span className="sr-only">Новый факт</span>
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Новый факт, например «живёт в Варшаве»" />
        </label>
        <Button type="submit" variant="outline" disabled={busy || !draft.trim()}>
          <Plus />
          Добавить
        </Button>
      </form>
    </div>
  );
}
