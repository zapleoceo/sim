import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCcw, Save } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { InlineError } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';
import { qk } from '@/lib/query';
import type { Hobby, LifePerson, PersonaLife, Place, RoutineSlot } from '@/lib/types';
import { usePersonaId } from '../persona-scope';
import { lifeApi } from './life-api';
import { newHobby, newPerson, newPlace, newRoutineSlot, sameJson, worldErrors } from './life-logic';
import { HomeEditor, LookEditor } from './look-home-editor';
import { RowsEditor, type Column } from './rows-editor';

const ROUTINE_COLUMNS: Column<RoutineSlot>[] = [
  { key: 'days', label: 'Дни', kind: 'days', width: '15rem' },
  { key: 'start', label: 'С', kind: 'time', width: '6.5rem' },
  { key: 'end', label: 'До', kind: 'time', width: '6.5rem' },
  { key: 'activity', label: 'Что делает', kind: 'textarea', width: 'minmax(10rem,2fr)' },
  { key: 'place', label: 'Где', kind: 'text', width: 'minmax(8rem,1fr)' },
  { key: 'with_whom', label: 'С кем', kind: 'names', width: 'minmax(7rem,1fr)' },
  { key: 'busy', label: 'Занятость', kind: 'busy', width: '8rem' },
];
const PEOPLE_COLUMNS: Column<LifePerson>[] = [
  { key: 'name', label: 'Имя', kind: 'text', width: 'minmax(7rem,1fr)' },
  { key: 'relation', label: 'Кто ей', kind: 'text', width: 'minmax(8rem,1fr)' },
  { key: 'traits', label: 'Какой человек', kind: 'textarea', width: 'minmax(10rem,2fr)' },
  { key: 'contact', label: 'Как общаются', kind: 'text', width: 'minmax(8rem,1fr)' },
  { key: 'notes', label: 'Заметки', kind: 'textarea', width: 'minmax(8rem,1.5fr)' },
];
const HOBBY_COLUMNS: Column<Hobby>[] = [
  { key: 'name', label: 'Увлечение', kind: 'text', width: 'minmax(8rem,1fr)' },
  { key: 'details', label: 'Подробности', kind: 'textarea', width: 'minmax(12rem,3fr)' },
  { key: 'frequency', label: 'Как часто', kind: 'text', width: 'minmax(8rem,1fr)' },
];
const PLACE_COLUMNS: Column<Place>[] = [
  { key: 'name', label: 'Место', kind: 'text', width: 'minmax(8rem,1fr)' },
  { key: 'kind', label: 'Что это', kind: 'text', width: 'minmax(6rem,0.7fr)' },
  { key: 'details', label: 'Подробности', kind: 'textarea', width: 'minmax(12rem,3fr)' },
];

/** Распорядок, Люди, Увлечения, Места, Облик, Дом: one document (`persona.life`), one Save. */
export function WorldEditor({ life }: { life: PersonaLife }) {
  const queryClient = useQueryClient();
  const personaId = usePersonaId();
  const [baseline, setBaseline] = useState(life);
  const [draft, setDraft] = useState(life);
  const [errors, setErrors] = useState<string[]>([]);
  const dirty = !sameJson(draft, baseline);
  useUnsavedGuard(dirty);

  const save = useMutation({
    mutationFn: (next: PersonaLife) => lifeApi.saveWorld(personaId, next),
    onSuccess: (saved) => {
      queryClient.setQueryData(qk.life(personaId), saved);
      setBaseline(saved);
      setDraft(saved);
      toast.success('Мир персонажа сохранён');
    },
  });
  const set = <K extends keyof PersonaLife>(key: K) => (rows: PersonaLife[K]) => setDraft((d) => ({ ...d, [key]: rows }));

  return (
    <div className="flex flex-col gap-3">
      <Tabs defaultValue="routine">
        <TabsList aria-label="Мир персонажа">
          <TabsTrigger value="routine">Распорядок ({draft.routine.length})</TabsTrigger>
          <TabsTrigger value="people">Люди ({draft.people.length})</TabsTrigger>
          <TabsTrigger value="hobbies">Увлечения ({draft.hobbies.length})</TabsTrigger>
          <TabsTrigger value="places">Места ({draft.places.length})</TabsTrigger>
          <TabsTrigger value="look">Облик</TabsTrigger>
          <TabsTrigger value="home">Дом ({draft.home.rooms.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="routine">
          <RowsEditor title="Распорядок" rows={draft.routine} columns={ROUTINE_COLUMNS} onChange={set('routine')}
            newRow={newRoutineSlot} addLabel="Добавить строку" emptyText="Распорядок пуст: без него план дня придумывается с нуля." />
        </TabsContent>
        <TabsContent value="people">
          <RowsEditor title="Люди" rows={draft.people} columns={PEOPLE_COLUMNS} onChange={set('people')}
            newRow={newPerson} addLabel="Добавить человека" emptyText="Никого нет." />
        </TabsContent>
        <TabsContent value="hobbies">
          <RowsEditor title="Увлечения" rows={draft.hobbies} columns={HOBBY_COLUMNS} onChange={set('hobbies')}
            newRow={newHobby} addLabel="Добавить увлечение" emptyText="Увлечений нет." />
        </TabsContent>
        <TabsContent value="places">
          <RowsEditor title="Места" rows={draft.places} columns={PLACE_COLUMNS} onChange={set('places')}
            newRow={newPlace} addLabel="Добавить место" emptyText="Мест нет." />
        </TabsContent>
        <TabsContent value="look">
          <LookEditor look={draft.look} onChange={set('look')} />
        </TabsContent>
        <TabsContent value="home">
          <HomeEditor home={draft.home} onChange={set('home')} />
        </TabsContent>
      </Tabs>
      {errors.length > 0 && <InlineError>{errors.join('; ')}</InlineError>}
      <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-3">
        <span className="mr-auto text-sm text-muted-foreground" aria-live="polite">
          {dirty ? 'Мир изменён, не сохранён' : 'Мир сохранён'}
        </span>
        <Button variant="ghost" disabled={!dirty || save.isPending} onClick={() => { setDraft(baseline); setErrors([]); }}>
          <RotateCcw />
          Отменить
        </Button>
        <Button
          disabled={!dirty || save.isPending}
          onClick={() => {
            const found = worldErrors(draft);
            setErrors(found);
            if (!found.length) save.mutate(draft);
          }}
        >
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Сохранить мир
        </Button>
      </div>
    </div>
  );
}
