import { NativeSelect } from '@/components/ui/native-select';

/** The «Персонаж» filter of chats and leads: 0 = every persona. */
export function PersonaFilter({ value, onChange, personas }: {
  value: number;
  onChange: (personaId: number) => void;
  personas: readonly { id: number; name: string }[];
}) {
  return (
    <NativeSelect aria-label="Персонаж" value={String(value)} onChange={(e) => onChange(Number(e.target.value) || 0)}>
      <option value="0">Все персонажи</option>
      {personas.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </NativeSelect>
  );
}
