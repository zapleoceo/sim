import { z } from 'zod';
import type { Persona, PersonaIn } from '@/lib/models';
import { GEMINI_VOICES } from './persona-voices';

export const AVATAR_URL_PATTERN = /^(https?:\/\/|\/)\S+$/i;

export const EMPTY_PERSONA: PersonaIn = {
  name: '',
  age: null,
  gender: 'female',
  avatar_url: '',
  description: '',
  prohibitions: '',
  language: 'ru',
  model: '',
  voice_id: '',
  voice_mode: 'off',
  voice_style: '',
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
 * Persona form. Required: name, description, language (free text up to 200 characters, e.g.
 * «русский, с клиентом из Украины — по-украински»). There is no «are you a bot?» field: the API
 * dropped `disclosure_text`; shared prompts live on /settings. Age is text in the form, converted on save.
 */
export const personaSchema = z.object({
  name: z.string().refine(notBlank, 'Укажите имя').refine((v) => v.length <= 80, 'Не длиннее 80 символов'),
  age: z.string().refine((v) => {
    if (v.trim() === '') return true;
    const n = Number(v);
    return Number.isInteger(n) && n >= 18 && n <= 100;
  }, '18–100'),
  gender: z.string().refine(notBlank, 'Укажите пол'),
  avatar_url: z
    .string()
    .max(500, 'Не длиннее 500 символов')
    .refine((v) => v === '' || AVATAR_URL_PATTERN.test(v), 'Нужна ссылка вида https://…'),
  description: z.string().refine(notBlank, 'Опишите персонажа').refine((v) => v.length <= 20000, 'Слишком длинно'),
  prohibitions: z.string().max(10000, 'Слишком длинно'),
  language: z.string().refine(notBlank, 'Укажите язык').refine((v) => v.length <= 200, 'Не длиннее 200 символов'),
  model: z.string().max(200),
  voice_id: z.string().refine((v) => v === '' || GEMINI_VOICE_IDS.has(v), 'Выберите голос из списка'),
  voice_mode: z.enum(['off', 'mirror', 'always']),
  voice_style: z.string().max(300, 'Не длиннее 300 символов'),
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
    gender: p.gender || 'female',
    avatar_url: p.avatar_url ?? '',
    description: p.description,
    prohibitions: p.prohibitions ?? '',
    language: p.language,
    model: p.model ?? '',
    voice_id: p.voice_id ?? '',
    voice_mode: p.voice_mode ?? 'off',
    voice_style: p.voice_style ?? '',
    strategy_id: p.strategy_id,
    is_active: p.is_active,
  };
}

/** Form -> API body (exactly the PersonaIn keys). */
export function personaFromForm(v: PersonaFormValues): PersonaIn {
  return {
    name: v.name.trim(),
    age: v.age.trim() === '' ? null : Number(v.age),
    gender: v.gender,
    avatar_url: v.avatar_url.trim(),
    description: v.description,
    prohibitions: v.prohibitions,
    language: v.language.trim(),
    model: v.model,
    voice_id: v.voice_id,
    voice_mode: v.voice_mode,
    voice_style: v.voice_style.trim(),
    strategy_id: v.strategy_id,
    is_active: v.is_active,
  };
}

export function personaToInput(p: Persona): PersonaIn {
  return {
    name: p.name,
    age: p.age,
    gender: p.gender,
    avatar_url: p.avatar_url,
    description: p.description,
    prohibitions: p.prohibitions,
    language: p.language,
    model: p.model,
    voice_id: p.voice_id,
    voice_mode: p.voice_mode,
    voice_style: p.voice_style,
    strategy_id: p.strategy_id,
    is_active: p.is_active,
  };
}

/** Non-empty rules in «Чего нельзя» (one rule per line). */
export function countRules(value: string): number {
  return value.split(/\r?\n/).filter((line) => line.trim() !== '').length;
}
