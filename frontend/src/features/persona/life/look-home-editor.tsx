import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import type { Home, Look, Room } from '@/lib/types';
import { LOOK_FIELDS, newRoom } from './life-logic';
import { RowsEditor, type Column } from './rows-editor';

const ROOM_COLUMNS: Column<Room>[] = [
  { key: 'name', label: 'Комната', kind: 'text', width: 'minmax(8rem,1fr)' },
  { key: 'details', label: 'Как выглядит', kind: 'textarea', width: 'minmax(14rem,4fr)' },
];

/** Constant details of her look: the same on every photo and video (docs/persona-life.md). */
export function LookEditor({ look, onChange }: { look: Look; onChange: (look: Look) => void }) {
  return (
    <section className="flex flex-col gap-3" aria-label="Облик">
      <p className="text-sm text-muted-foreground">
        Одинаковы на всех фото, видео и кружках, пока вы их не поменяете. Пустое поле — как на референсах внешности.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {LOOK_FIELDS.map(({ key, label, placeholder }) => (
          <div key={key} className="flex flex-col gap-1.5">
            <Label htmlFor={`look-${key}`}>{label}</Label>
            <Textarea
              id={`look-${key}`}
              rows={2}
              maxLength={300}
              placeholder={placeholder}
              value={look[key]}
              onChange={(e) => onChange({ ...look, [key]: e.target.value })}
            />
          </div>
        ))}
      </div>
    </section>
  );
}

/** Her one apartment: every picture at home shows one of these rooms. */
export function HomeEditor({ home, onChange }: { home: Home; onChange: (home: Home) => void }) {
  return (
    <section className="flex flex-col gap-3" aria-label="Дом">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="home-details">Квартира целиком</Label>
        <Textarea
          id="home-details"
          rows={3}
          maxLength={1000}
          placeholder="Старый дом, 4 этаж, высокие потолки, белые стены, дубовый паркет…"
          value={home.details}
          onChange={(e) => onChange({ ...home, details: e.target.value })}
        />
      </div>
      <p className="text-sm text-muted-foreground">
        Фото дома всегда в одной из этих комнат; первая — если не понятно, в какой. Первое фото комнаты становится её образцом.
      </p>
      <RowsEditor title="Комнаты" rows={home.rooms} columns={ROOM_COLUMNS} onChange={(rooms) => onChange({ ...home, rooms })}
        newRow={newRoom} addLabel="Добавить комнату" emptyText="Комнат нет: дома она в «квартире вообще»." />
    </section>
  );
}
