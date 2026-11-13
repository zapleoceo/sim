import { z } from 'zod';
import type { Persona, PersonaIn } from '@/lib/models';
import { GEMINI_VOICES } from './persona-voices';

export const AVATAR_URL_PATTERN = /^(https?:\/\/|\/)\S+$/i;

export const EMPTY_PERSONA: PersonaIn = {
  name: '',
  age: null,
  birth_date: null,
  timezone: 'Europe/Kyiv',
  gender: 'female',
  avatar_url: '',
  description: '',
  prohibitions: '',
  language: 'ru',
  model: '',
  voice_id: '',
  voice_mode: 'off',
  voice_style: '',
  voice_ambience: 'off',
  strategy_id: null,
  is_active: true,
};

export const GENDERS = [
  { value: 'female', label: 'Женский' },
  { value: 'male', label: 'Мужской' },
  { value: 'other', label: 'Другое' },
] as const;

const GEMINI_VOICE_IDS = new Set(GEMINI_VOICES.map((v) => v.id));

const notBlank = (v: string): boolean => v.trim() !== '';

/**
 * Persona form. The editor shows only name, model, strategy, avatar, description and voice: age,
 * gender, language and prohibitions are not edited here (origin lives in the description; age, zodiac
 * and local time come from the birth date and time zone, computed by code;
 * restrictions are set later in the simulator). Their saved values pass through unchanged; empty
 * gender/language fall back to female/ru. Required: name and description.
 */
export const personaSchema = z.object({
  name: z.string().refine(notBlank, 'Укажите имя').refine((v) => v.length <= 80, 'Не длиннее 80 символов'),
  age: z.string().refine((v) => {
    if (v.trim() === '') return true;
    const n = Number(v);
    return Number.isInteger(n) && n >= 18 && n <= 100;
  }, '18–100'),
  birth_date: z.string().refine((v) => v === '' || (/^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v))), 'Дата вида 1976-12-09'),
  timezone: z.string().refine(notBlank, 'Укажите часовой пояс').refine((v) => v.length <= 64, 'Не длиннее 64 символов'),
  gender: z.string(),
  avatar_url: z
    .string()
    .max(500, 'Не длиннее 500 символов')
    .refine((v) => v === '' || AVATAR_URL_PATTERN.test(v), 'Нужна ссылка вида https://…'),
  description: z.string().refine(notBlank, 'Опишите персонажа').refine((v) => v.length <= 20000, 'Слишком длинно'),
  prohibitions: z.string().max(10000, 'Слишком длинно'),
  language: z.string().max(200, 'Не длиннее 200 символов'),
  model: z.string().max(200),
  voice_id: z.string().refine((v) => v === '' || GEMINI_VOICE_IDS.has(v), 'Выберите голос из списка'),
  voice_mode: z.enum(['off', 'mirror', 'always']),
  voice_style: z.string().max(300, 'Не длиннее 300 символов'),
  voice_ambience: z.enum(['off', 'room', 'street', 'cafe', 'random']),
  strategy_id: z.number().nullable(),
  is_active: z.boolean(),
}).refine((v) => v.voice_mode === 'off' || v.voice_id !== '', {
  path: ['voice_id'],
  message: 'Для голосовых ответов выберите голос',
});

export type PersonaFormValues = z.infer<typeof personaSchema>;

/** Field name -> first error message (empty object when valid). Handy for tests and guards. */
export function personaErrors(values: PersonaFormValues): Partial<Record<keyof PersonaFormValues, string>> {
  const r = personaSchema.safeParse(values);
  if (r.success) return {};
  const out: Partial<Record<keyof PersonaFormValues, string>> = {};
  for (const issue of r.error.issues) {
    const key = issue.path[0] as keyof PersonaFormValues;
    out[key] ??= issue.message;
  }
  return out;
}

export function personaToForm(p: PersonaIn): PersonaFormValues {
  return {
    name: p.name,
    age: p.age == null ? '' : String(p.age),
    birth_date: p.birth_date ?? '',
    timezone: p.timezone || 'Europe/Kyiv',
    gender: p.gender || 'female',
    avatar_url: p.avatar_url ?? '',
    description: p.description,
    prohibitions: p.prohibitions ?? '',
    language: p.language,
    model: p.model ?? '',
    voice_id: p.voice_id ?? '',
    voice_mode: p.voice_mode ?? 'off',
    voice_style: p.voice_style ?? '',
    voice_ambience: p.voice_ambience ?? 'off',
    strategy_id: p.strategy_id,
    is_active: p.is_active,
  };
}

/** Form -> API body (exactly the PersonaIn keys). */
export function personaFromForm(v: PersonaFormValues): PersonaIn {
  return {
    name: v.name.trim(),
    age: v.age.trim() === '' ? null : Number(v.age),
    birth_date: v.birth_date || null,
    timezone: v.timezone.trim(),
    gender: v.gender || 'female',
    avatar_url: v.avatar_url.trim(),
    description: v.description,
    prohibitions: v.prohibitions,
    language: v.language.trim() || 'ru',
    model: v.model,
    voice_id: v.voice_id,
    voice_mode: v.voice_mode,
    voice_style: v.voice_style.trim(),
    voice_ambience: v.voice_ambience,
    strategy_id: v.strategy_id,
    is_active: v.is_active,
  };
}

export function personaToInput(p: Persona): PersonaIn {
  return {
    name: p.name,
    age: p.age,
    birth_date: p.birth_date ?? null,
    timezone: p.timezone || 'Europe/Kyiv',
    gender: p.gender,
    avatar_url: p.avatar_url,
    description: p.description,
    prohibitions: p.prohibitions,
    language: p.language,
    model: p.model,
    voice_id: p.voice_id,
    voice_mode: p.voice_mode,
    voice_style: p.voice_style,
    voice_ambience: p.voice_ambience,
    strategy_id: p.strategy_id,
    is_active: p.is_active,
  };
}
