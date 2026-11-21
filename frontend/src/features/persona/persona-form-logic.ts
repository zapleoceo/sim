import { z } from 'zod';
import type { Persona, PersonaPatch } from '@/lib/types';

export const PROMPT_SOFT_LIMIT = 60_000;
export const REPLY_DELAY_MAX = 120;

const notBlank = (v: string): boolean => v.trim() !== '';
const text = z.string();
const flag = z.boolean();

/**
 * The persona form: her own settings, each edited here and only here (docs/frontend.md); her agents
 * and the guard's rules live in «Агенты».
 * All values are strings/booleans, so the form input and the validated output are the same type.
 */
export const personaSchema = z.object({
  name: z.string().refine(notBlank, 'Укажите имя').refine((v) => v.length <= 120, 'Не длиннее 120 символов'),
  gender: text,
  language: text.refine(notBlank, 'Укажите язык'),
  timezone: z.string().refine(notBlank, 'Укажите часовой пояс').refine((v) => v.length <= 64, 'Не длиннее 64 символов'),
  birth_date: z.string().refine((v) => v === '' || (/^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v))), 'Дата вида 1999-12-31'),
  prompt: text,
  appearance: text,
  voice_id: text,
  voice_style: text,
  ai_enabled: flag,
  voice_enabled: flag,
  photo_enabled: flag,
  video_enabled: flag,
  calls_enabled: flag,
  voice_ambience_enabled: flag,
  reply_delay_s: z.string().refine((v) => {
    const n = Number(v);
    return v.trim() !== '' && Number.isInteger(n) && n >= 0 && n <= REPLY_DELAY_MAX;
  }, `Целое число от 0 до ${REPLY_DELAY_MAX}`),
});

export type PersonaFormValues = z.infer<typeof personaSchema>;
export type PersonaFormField = keyof PersonaFormValues;

export function toFormValues(p: Persona): PersonaFormValues {
  return {
    name: p.name,
    gender: p.gender || 'female',
    language: p.language || 'ru',
    timezone: p.timezone,
    birth_date: p.birth_date ?? '',
    prompt: p.prompt,
    appearance: p.appearance,
    voice_id: p.voice_id,
    voice_style: p.voice_style,
    ai_enabled: p.ai_enabled,
    voice_enabled: p.voice_enabled,
    photo_enabled: p.photo_enabled,
    video_enabled: p.video_enabled,
    calls_enabled: p.calls_enabled,
    voice_ambience_enabled: p.voice_ambience_enabled,
    reply_delay_s: String(p.reply_delay_s),
  };
}

/** Only the changed fields, converted back to API types (PUT /personas/{id} is a partial update). */
export function toPatch(values: PersonaFormValues, initial: PersonaFormValues): PersonaPatch {
  const patch: Record<string, unknown> = {};
  for (const key of Object.keys(values) as PersonaFormField[]) {
    if (values[key] === initial[key]) continue;
    if (key === 'birth_date') patch.birth_date = values.birth_date || null;
    else if (key === 'reply_delay_s') patch.reply_delay_s = Number(values.reply_delay_s);
    else if (key === 'name' || key === 'timezone') patch[key] = values[key].trim();
    else patch[key] = values[key];
  }
  return patch as PersonaPatch;
}

export const GENDERS = [
  { value: 'female', label: 'Женский' },
  { value: 'male', label: 'Мужской' },
] as const;

export const LANGUAGES = [
  { value: 'ru', label: 'Русский' },
  { value: 'uk', label: 'Українська' },
  { value: 'en', label: 'English' },
] as const;

/** Options + the current value when it is not among them (never lose a saved value). */
export function withCurrent<T extends { value: string; label: string }>(options: readonly T[], current: string): { value: string; label: string }[] {
  const list = options.map((o) => ({ value: o.value, label: o.label }));
  return current && !list.some((o) => o.value === current) ? [{ value: current, label: current }, ...list] : list;
}

/** IANA zones for the timezone suggestions (falls back to a short list on old engines). */
export function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf('timeZone');
  } catch {
    return ['Europe/Kyiv', 'Europe/Moscow', 'Europe/Warsaw', 'Europe/Berlin', 'Asia/Dubai', 'UTC'];
  }
}
