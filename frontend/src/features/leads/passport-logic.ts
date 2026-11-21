import type { LeadStage, Passport, PassportField, PassportPatch } from '@/lib/types';

/** Stages in funnel order with their Russian labels (docs/leads.md). */
export const STAGES: readonly { value: LeadStage; label: string; variant: 'muted' | 'secondary' | 'warning' | 'success' | 'destructive' }[] = [
  { value: 'new', label: 'Новый', variant: 'muted' },
  { value: 'acquainted', label: 'Знакомство', variant: 'secondary' },
  { value: 'interested', label: 'Интерес', variant: 'secondary' },
  { value: 'warm', label: 'Тёплый', variant: 'warning' },
  { value: 'hot', label: 'Горячий', variant: 'destructive' },
  { value: 'client', label: 'Клиент', variant: 'success' },
  { value: 'lost', label: 'Потерян', variant: 'muted' },
];

export function stageInfo(stage: LeadStage): (typeof STAGES)[number] {
  return STAGES.find((s) => s.value === stage) ?? STAGES[0];
}

export function isStage(value: unknown): value is LeadStage {
  return typeof value === 'string' && STAGES.some((s) => s.value === value);
}

/** Labels of the structured fields, in the order of the card. */
export const PASSPORT_FIELDS: readonly { field: PassportField; label: string }[] = [
  { field: 'name', label: 'Имя' },
  { field: 'age', label: 'Возраст' },
  { field: 'gender', label: 'Пол' },
  { field: 'city', label: 'Город' },
  { field: 'country', label: 'Страна' },
  { field: 'timezone', label: 'Часовой пояс' },
  { field: 'occupation', label: 'Чем занимается' },
  { field: 'stage', label: 'Стадия' },
  { field: 'temperature', label: 'Теплота' },
  { field: 'next_step', label: 'Следующий шаг' },
];

/** The editable form: every value as a string, like the inputs hold them. */
export interface PassportForm {
  text: string;
  name: string;
  age: string;
  gender: string;
  city: string;
  country: string;
  timezone: string;
  occupation: string;
  stage: LeadStage;
  temperature: string;
  next_step: string;
}

export function toPassportForm(p: Passport): PassportForm {
  return {
    text: p.text, name: p.name, age: p.age === null ? '' : String(p.age), gender: p.gender, city: p.city,
    country: p.country, timezone: p.timezone, occupation: p.occupation, stage: p.stage,
    temperature: String(p.temperature), next_step: p.next_step,
  };
}

export function passportFormErrors(f: PassportForm): string[] {
  const errors: string[] = [];
  if (f.age.trim() !== '') {
    const age = Number(f.age);
    if (!Number.isInteger(age) || age < 1 || age > 119) errors.push('Возраст — целое число от 1 до 119');
  }
  const t = Number(f.temperature);
  if (f.temperature.trim() === '' || !Number.isInteger(t) || t < 0 || t > 100) errors.push('Теплота — целое число от 0 до 100');
  if (f.timezone.trim() && !/^[A-Za-z_]+(\/[A-Za-z0-9_+-]+)+$|^UTC$/.test(f.timezone.trim())) {
    errors.push('Часовой пояс вида Europe/Warsaw');
  }
  return errors;
}

/**
 * Only what the operator changed, in API types. Every structured field sent becomes locked on
 * the server (the passport writer never overwrites it); the text is not locked.
 */
export function passportPatch(values: PassportForm, initial: PassportForm): PassportPatch {
  const patch: PassportPatch = {};
  const set = <K extends keyof PassportPatch>(key: K, value: PassportPatch[K]): void => {
    patch[key] = value;
  };
  if (values.text !== initial.text) set('text', values.text.trim());
  for (const key of ['name', 'gender', 'city', 'country', 'timezone', 'occupation', 'next_step'] as const) {
    if (values[key] !== initial[key]) set(key, values[key].trim());
  }
  if (values.age !== initial.age) set('age', values.age.trim() === '' ? null : Number(values.age));
  if (values.stage !== initial.stage) set('stage', values.stage);
  if (values.temperature !== initial.temperature) set('temperature', Number(values.temperature));
  return patch;
}

/** Unlock one field: the passport writer may update it again. */
export function unlockPatch(locked: readonly PassportField[], field: PassportField): PassportPatch {
  return { locked_fields: locked.filter((f) => f !== field) };
}

/** The first lines of the passport text for the short view in the chat. */
export function passportPreview(text: string, maxLines = 8): string {
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length > maxLines ? `${lines.slice(0, maxLines).join('\n')}\n…` : lines.join('\n');
}
