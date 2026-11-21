import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Save, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import { qk } from '@/lib/query';
import type { Arc, ArcStatus } from '@/lib/types';
import { usePersonaId } from '../persona-scope';
import { lifeApi, type ArcInput } from './life-api';
import { sameJson } from './life-logic';

const STATUSES: { value: ArcStatus; label: string }[] = [
  { value: 'active', label: 'идёт' },
  { value: 'paused', label: 'на паузе' },
  { value: 'done', label: 'закончилась' },
];

const editable = (arc: Arc): ArcInput => ({
  title: arc.title, summary: arc.summary, stage: arc.stage, status: arc.status, ends_on: arc.ends_on,
});

function ArcCard({ arc }: { arc: Arc }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [draft, setDraft] = useState(() => editable(arc));
  const dirty = !sameJson(draft, editable(arc));
  const personaId = usePersonaId();
  const refresh = () => queryClient.invalidateQueries({ queryKey: qk.arcs(personaId) });
  const save = useMutation({
    mutationFn: () => lifeApi.updateArc(personaId, arc.id, draft),
    onSuccess: async (saved) => {
      setDraft(editable(saved));
      await refresh();
      toast.success('История сохранена');
    },
  });
  const remove = useMutation({ mutationFn: () => lifeApi.deleteArc(personaId, arc.id), onSuccess: refresh });
  const id = `arc-${arc.id}`;
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_10rem_10rem]">
        <Input aria-label={`Название истории ${arc.id}`} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
        <NativeSelect aria-label={`Статус истории ${arc.id}`} value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value as ArcStatus })}>
          {STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </NativeSelect>
        <Input aria-label={`Закончится ${arc.id}`} type="date" value={draft.ends_on ?? ''} onChange={(e) => setDraft({ ...draft, ends_on: e.target.value || null })} />
      </div>
      <Textarea id={`${id}-summary`} aria-label={`Суть истории ${arc.id}`} rows={2} value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} />
      <Textarea id={`${id}-stage`} aria-label={`Стадия истории ${arc.id}`} rows={2} placeholder="Где история сейчас" value={draft.stage} onChange={(e) => setDraft({ ...draft, stage: e.target.value })} />
      <div className="flex flex-wrap items-center justify-end gap-2">
        <span className="mr-auto text-xs text-muted-foreground">с {arc.started_on}</span>
        <Button
          variant="ghost-destructive"
          size="sm"
          disabled={remove.isPending}
          onClick={async () => {
            const ok = await confirm({ title: 'Удалить историю?', message: `«${arc.title}» пропадёт из её жизни.`, confirmLabel: 'Удалить', danger: true });
            if (ok) remove.mutate();
          }}
        >
          <Trash2 />
          Удалить
        </Button>
        <Button size="sm" disabled={!dirty || !draft.title.trim() || save.isPending} onClick={() => save.mutate()}>
          <Save />
          Сохранить
        </Button>
      </div>
    </li>
  );
}

/** Story arcs: what slowly happens in her life (the planner moves their stage). */
export function ArcsEditor({ arcs }: { arcs: readonly Arc[] }) {
  const personaId = usePersonaId();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const create = useMutation({
    mutationFn: () => lifeApi.createArc(personaId, { title: title.trim(), summary: summary.trim(), stage: '' }),
    onSuccess: async () => {
      setTitle('');
      setSummary('');
      await queryClient.invalidateQueries({ queryKey: qk.arcs(personaId) });
      toast.success('История добавлена');
    },
  });
  return (
    <div className="flex flex-col gap-3">
      {arcs.length === 0 && <p className="text-sm text-muted-foreground">Историй нет.</p>}
      <ul className="flex flex-col gap-3">
        {arcs.map((arc) => <ArcCard key={`${arc.id}-${arc.updated_at}`} arc={arc} />)}
      </ul>
      <form
        className="flex flex-col gap-2 rounded-lg border border-dashed p-3 md:flex-row md:items-start"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) create.mutate();
        }}
      >
        <Input aria-label="Новая история" placeholder="Новая история" value={title} onChange={(e) => setTitle(e.target.value)} className="md:max-w-72" />
        <Textarea aria-label="Суть новой истории" placeholder="Суть" rows={1} value={summary} onChange={(e) => setSummary(e.target.value)} />
        <Button type="submit" variant="outline" disabled={!title.trim() || create.isPending}>
          <Plus />
          Добавить историю
        </Button>
      </form>
    </div>
  );
}
