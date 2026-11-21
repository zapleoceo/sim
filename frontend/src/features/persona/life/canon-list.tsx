import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Ban, Check, Pencil, Undo2, X } from 'lucide-react';
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useDraft } from '@/hooks/use-draft';
import { formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query';
import type { CanonFact } from '@/lib/types';
import { cn } from '@/lib/utils';
import { usePersonaId } from '../persona-scope';
import { lifeApi } from './life-api';

function CanonRow({ item }: { item: CanonFact }) {
  const personaId = usePersonaId();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const { draft: text, setDraft: setText, dirty, reset, commit } = useDraft(item.fact, { guardName: 'Канон' });
  const close = (): void => {
    reset();
    setEditing(false);
  };
  const update = useMutation({
    mutationFn: (patch: Partial<Pick<CanonFact, 'fact' | 'status'>>) => lifeApi.updateCanon(personaId, item.id, patch),
    onSuccess: async (saved) => {
      commit(saved.fact);
      setEditing(false);
      await queryClient.invalidateQueries({ queryKey: qk.canon(personaId) });
    },
  });
  const rejected = item.status === 'rejected';
  if (editing) {
    return (
      <li className="py-2">
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (dirty && text.trim().length >= 3) update.mutate({ fact: text.trim() });
          }}
        >
          <Input aria-label="Факт канона" value={text} onChange={(e) => setText(e.target.value)} autoFocus />
          <Button type="submit" variant="ghost" size="icon-sm" disabled={update.isPending || !dirty || text.trim().length < 3} aria-label="Сохранить факт">
            <Check />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={close} aria-label="Отменить">
            <X />
          </Button>
        </form>
      </li>
    );
  }
  return (
    <li className="flex items-start gap-2 py-2">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className={cn('text-sm break-words', rejected && 'text-muted-foreground line-through')}>{item.fact}</span>
        <span className="text-xs text-muted-foreground">
          {formatDateTime(item.created_at)}
          {item.chat_id !== null && ` · чат ${item.chat_id}`}
        </span>
      </div>
      {rejected && <Badge variant="muted">отклонён</Badge>}
      <Button variant="ghost" size="icon-sm" onClick={() => setEditing(true)} aria-label={`Изменить: ${item.fact}`}>
        <Pencil />
      </Button>
      {rejected ? (
        <Button variant="ghost" size="icon-sm" disabled={update.isPending} onClick={() => update.mutate({ status: 'active' })} aria-label={`Вернуть: ${item.fact}`}>
          <Undo2 />
        </Button>
      ) : (
        <Button variant="ghost-destructive" size="icon-sm" disabled={update.isPending} onClick={() => update.mutate({ status: 'rejected' })} aria-label={`Отклонить: ${item.fact}`}>
          <Ban />
        </Button>
      )}
    </li>
  );
}

/** Canon: facts she invented about herself in chats; rejected ones are hidden from her. */
export function CanonList({ items }: { items: readonly CanonFact[] }) {
  if (!items.length) {
    return <p className="text-sm text-muted-foreground">Пока ничего: факты появятся, когда она что-то о себе придумает в переписке.</p>;
  }
  return (
    <ul className="flex flex-col divide-y">
      {/* Keyed by id only: a fact changed on the server reaches the row's draft instead of remounting it. */}
      {items.map((item) => <CanonRow key={item.id} item={item} />)}
    </ul>
  );
}
