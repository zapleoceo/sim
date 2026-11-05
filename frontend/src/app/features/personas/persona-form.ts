import { FormControl, FormGroup, Validators } from '@angular/forms';
import { Persona, PersonaIn } from '../../core/models';

export const AVATAR_URL_PATTERN = /^(https?:\/\/|\/)\S+$/i;

export type PersonaForm = FormGroup<{
  name: FormControl<string>;
  age: FormControl<number | null>;
  gender: FormControl<string>;
  avatar_url: FormControl<string>;
  description: FormControl<string>;
  prohibitions: FormControl<string>;
  language: FormControl<string>;
  model: FormControl<string>;
  voice_id: FormControl<string>;
  strategy_id: FormControl<number | null>;
  is_active: FormControl<boolean>;
  disclosure_text: FormControl<string>;
}>;

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
  strategy_id: null,
  is_active: true,
  disclosure_text: '',
};

const text = (value: string, validators = [Validators.maxLength(4000)]): FormControl<string> =>
  new FormControl(value, { nonNullable: true, validators });

/**
 * Форма персонажа. Обязательны: имя, описание, язык и текст раскрытия
 * («я виртуальный ассистент») — без него ИИ нечем честно ответить на прямой вопрос.
 */
export function createPersonaForm(initial: PersonaIn = EMPTY_PERSONA): PersonaForm {
  return new FormGroup({
    name: text(initial.name, [Validators.required, Validators.maxLength(80)]),
    age: new FormControl<number | null>(initial.age, [Validators.min(18), Validators.max(100)]),
    gender: text(initial.gender, [Validators.required]),
    avatar_url: text(initial.avatar_url, [Validators.pattern(AVATAR_URL_PATTERN), Validators.maxLength(500)]),
    description: text(initial.description, [Validators.required, Validators.maxLength(20000)]),
    prohibitions: text(initial.prohibitions, [Validators.maxLength(10000)]),
    language: text(initial.language, [Validators.required, Validators.maxLength(16)]),
    model: text(initial.model, [Validators.maxLength(200)]),
    voice_id: text(initial.voice_id, [Validators.maxLength(200)]),
    strategy_id: new FormControl<number | null>(initial.strategy_id),
    is_active: new FormControl(initial.is_active, { nonNullable: true }),
    disclosure_text: text(initial.disclosure_text, [Validators.required, Validators.maxLength(1000)]),
  });
}

/** Число непустых правил в поле «Чего нельзя» (одно правило на строку). */
export function countRules(value: string): number {
  return value.split(/\r?\n/).filter((line) => line.trim() !== '').length;
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
    strategy_id: p.strategy_id,
    is_active: p.is_active,
    disclosure_text: p.disclosure_text,
  };
}
