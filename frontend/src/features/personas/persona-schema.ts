import { z } from 'zod';
import type { Persona, PersonaIn } from '@/lib/models';
import { GEMINI_VOICES } from './persona-voices';

export const AVATAR_URL_PATTERN = /^(https?:\/\/|\/)\S+$/i;

/** Как backend `persona_routine.MAX_ROUTINE_LEN`; формат строк проверяет API. */
export const ROUTINE_MAX = 4000;

export const EMPTY_PERSONA: PersonaIn = {
  name: '',
  birth_date: null,
  timezone: 'Europe/Kyiv',
  routine: '',
  gender: 'female',
  avatar_url: '',
  agent_prompt: '',
  language: 'ru',
  model: '',
  identity_model: '',
  fallback_model: '',
  voice_id: '',
  voice_mode: 'off',
  voice_style: '',
  voice_ambience: 'off',
  accept_calls: false,
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

/** Как backend: предел длины единого промпта персонажа. */
export const AGENT_PROMPT_MAX = 50000;

/**
 * Persona form: every field of the persona is edited on its page, the prompt included. The age is not
 * stored: code counts it from the birth date. Empty gender and language fall back to female/ru.
 * Required: name, time zone and the prompt.
 */
export const personaSchema = z.object({
  name: z.string().refine(notBlank, 'Укажите имя').refine((v) => v.length <= 80, 'Не длиннее 80 символов'),
  birth_date: z.string().refine((v) => v === '' || (/^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v))), 'Дата вида 1976-12-09'),
  timezone: z.string().refine(notBlank, 'Укажите часовой пояс').refine((v) => v.length <= 64, 'Не длиннее 64 символов'),
  routine: z.string().max(ROUTINE_MAX, `Не длиннее ${ROUTINE_MAX} символов`),
  gender: z.string(),
  avatar_url: z
    .string()
    .max(500, 'Не длиннее 500 символов')
    .refine((v) => v === '' || AVATAR_URL_PATTERN.test(v), 'Нужна ссылка вида https://…'),
  agent_prompt: z
    .string()
    .refine(notBlank, 'Напишите промпт')
    .refine((v) => v.length <= AGENT_PROMPT_MAX, `Не длиннее ${AGENT_PROMPT_MAX} символов`),
  language: z.string().max(200, 'Не длиннее 200 символов'),
  model: z.string().max(200),
  identity_model: z.string().max(120),
  fallback_model: z.string().max(120),
  voice_id: z.string().refine((v) => v === '' || GEMINI_VOICE_IDS.has(v), 'Выберите голос из списка'),
  voice_mode: z.enum(['off', 'mirror', 'always']),
  voice_style: z.string().max(300, 'Не длиннее 300 символов'),
  voice_ambience: z.enum(['off', 'room', 'street', 'cafe', 'random', 'contextual']),
  accept_calls: z.boolean(),
  strategy_id: z.number().nullable(),
  is_active: z.boolean(),
}).refine((v) => !v.accept_calls || v.voice_id !== '', {
  path: ['voice_id'],
  message: 'Для звонков выберите голос',
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
    birth_date: p.birth_date ?? '',
    timezone: p.timezone || 'Europe/Kyiv',
    routine: p.routine ?? '',
    gender: p.gender || 'female',
    avatar_url: p.avatar_url ?? '',
    agent_prompt: p.agent_prompt ?? '',
    language: p.language,
    model: p.model ?? '',
    identity_model: p.identity_model ?? '',
    fallback_model: p.fallback_model ?? '',
    voice_id: p.voice_id ?? '',
    voice_mode: p.voice_mode ?? 'off',
    voice_style: p.voice_style ?? '',
    voice_ambience: p.voice_ambience ?? 'off',
    accept_calls: p.accept_calls ?? false,
    strategy_id: p.strategy_id,
    is_active: p.is_active,
  };
}

/** Form -> API body (exactly the PersonaIn keys). */
export function personaFromForm(v: PersonaFormValues): PersonaIn {
  return {
    name: v.name.trim(),
    birth_date: v.birth_date || null,
    timezone: v.timezone.trim(),
    routine: v.routine,
    gender: v.gender || 'female',
    avatar_url: v.avatar_url.trim(),
    agent_prompt: v.agent_prompt,
    language: v.language.trim() || 'ru',
    model: v.model,
    identity_model: v.identity_model,
    fallback_model: v.fallback_model,
    voice_id: v.voice_id,
    voice_mode: v.voice_mode,
    voice_style: v.voice_style.trim(),
    voice_ambience: v.voice_ambience,
    accept_calls: v.accept_calls,
    strategy_id: v.strategy_id,
    is_active: v.is_active,
  };
}

export function personaToInput(p: Persona): PersonaIn {
  return {
    name: p.name,
    birth_date: p.birth_date ?? null,
    timezone: p.timezone || 'Europe/Kyiv',
    routine: p.routine ?? '',
    gender: p.gender,
    avatar_url: p.avatar_url,
    agent_prompt: p.agent_prompt ?? '',
    language: p.language,
    model: p.model,
    identity_model: p.identity_model ?? '',
    fallback_model: p.fallback_model ?? '',
    voice_id: p.voice_id,
    voice_mode: p.voice_mode,
    voice_style: p.voice_style,
    voice_ambience: p.voice_ambience,
    accept_calls: p.accept_calls ?? false,
    strategy_id: p.strategy_id,
    is_active: p.is_active,
  };
}

/** Full years since the birth date (`YYYY-MM-DD`), as the backend counts them; null when the date is not set. */
export function ageFromBirthDate(value: string, today: Date = new Date()): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const [year, month, day] = match.slice(1).map(Number);
  const hadBirthday = today.getMonth() + 1 > month || (today.getMonth() + 1 === month && today.getDate() >= day);
  return today.getFullYear() - year - (hadBirthday ? 0 : 1);
}
