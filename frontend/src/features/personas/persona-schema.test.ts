import type { Persona } from '@/lib/models';
import {
  AGENT_PROMPT_MAX, EMPTY_PERSONA, ROUTINE_MAX, ageFromBirthDate, personaErrors, personaFromForm, personaToForm, personaToInput,
  type PersonaFormValues,
} from './persona-schema';
import { GEMINI_VOICES, VOICE_AMBIENCE_OPTIONS, VOICE_MODES, VOICE_OPTIONS } from './persona-voices';

const valid: PersonaFormValues = personaToForm({ ...EMPTY_PERSONA, name: 'Марина', agent_prompt: 'Ты Марина: тёплая, энергичная.' });

describe('persona form validation', () => {
  it('keeps separate model routes through save/reopen and leaves reserve disabled by default', () => {
    expect(personaFromForm(valid)).toMatchObject({ identity_model: '', fallback_model: '' });
    const body = personaFromForm({ ...valid, model: 'primary', identity_model: 'identity', fallback_model: 'codex-sub/reserve' });
    expect(personaToForm(body)).toMatchObject({ model: 'primary', identity_model: 'identity', fallback_model: 'codex-sub/reserve' });
    expect(body.agent_prompt).toBe(valid.agent_prompt);
  });
  it('is invalid when empty', () => {
    const errors = personaErrors(personaToForm(EMPTY_PERSONA));
    expect(errors.name).toBe('Укажите имя');
    expect(errors.agent_prompt).toBe('Напишите промпт');
  });

  it('has no «are you a bot» field: the API dropped disclosure_text', () => {
    expect(Object.keys(valid)).not.toContain('disclosure_text');
    expect(Object.keys(personaFromForm(valid))).not.toContain('disclosure_text');
  });

  it('accepts a descriptive language up to 200 characters', () => {
    expect(personaErrors({ ...valid, language: 'русский (переходит на украинский, если клиент пишет по-украински)' }).language).toBeUndefined();
    expect(personaErrors({ ...valid, language: 'x'.repeat(201) }).language).toBe('Не длиннее 200 символов');
  });

  it('is valid with the required fields filled', () => {
    expect(personaErrors(valid)).toEqual({});
  });

  it('does not block on empty fields: empty language and gender fall back to ru and female', () => {
    expect(personaErrors({ ...valid, language: '', gender: '' })).toEqual({});
    const body = personaFromForm({ ...valid, language: ' ', gender: '' });
    expect(body.language).toBe('ru');
    expect(body.gender).toBe('female');
    expect(personaErrors({ ...valid, name: 'x'.repeat(81) }).name).toBeDefined();
  });

  it('takes an exact birth date and a time zone; an empty date is null', () => {
    expect(personaErrors({ ...valid, birth_date: '1976-12-09' })).toEqual({});
    expect(personaErrors({ ...valid, birth_date: '09.12.1976' }).birth_date).toBe('Дата вида 1976-12-09');
    expect(personaErrors({ ...valid, timezone: ' ' }).timezone).toBe('Укажите часовой пояс');
    expect(personaFromForm({ ...valid, birth_date: '1976-12-09' }).birth_date).toBe('1976-12-09');
    expect(personaFromForm(valid)).toMatchObject({ birth_date: null, timezone: 'Europe/Kyiv' });
  });

  it('keeps the weekly routine as typed and limits its length', () => {
    const routine = 'пн-пт 09:00-13:00 консультации\nежедневно 23:00-06:30 сплю';
    expect(personaFromForm({ ...valid, routine }).routine).toBe(routine);
    expect(personaErrors({ ...valid, routine: 'x'.repeat(ROUTINE_MAX + 1) }).routine).toBe(`Не длиннее ${ROUTINE_MAX} символов`);
    expect(personaFromForm(valid).routine).toBe('');
  });

  it('requires the prompt, limits its length and sends it as typed', () => {
    expect(personaErrors({ ...valid, agent_prompt: '  \n ' }).agent_prompt).toBe('Напишите промпт');
    expect(personaErrors({ ...valid, agent_prompt: 'x'.repeat(AGENT_PROMPT_MAX + 1) }).agent_prompt)
      .toBe(`Не длиннее ${AGENT_PROMPT_MAX} символов`);
    expect(personaErrors({ ...valid, agent_prompt: 'x'.repeat(AGENT_PROMPT_MAX) }).agent_prompt).toBeUndefined();
    const prompt = 'Ты Марина.\n\nЗапреты:\n- не обещай скидок\n';
    expect(personaFromForm({ ...valid, agent_prompt: prompt }).agent_prompt).toBe(prompt);
  });

  it('has no age, description or prohibitions: the age comes from the birth date, the rest is in the prompt', () => {
    for (const key of ['age', 'description', 'prohibitions']) {
      expect(Object.keys(valid)).not.toContain(key);
      expect(Object.keys(personaFromForm(valid))).not.toContain(key);
    }
    expect(Object.keys(EMPTY_PERSONA)).toContain('agent_prompt');
  });

  it('accepts http(s) and root-relative avatar URLs only', () => {
    for (const ok of ['', 'https://cdn.example.com/a.png', '/media/a.png']) {
      expect(personaErrors({ ...valid, avatar_url: ok }).avatar_url, ok).toBeUndefined();
    }
    for (const bad of ['javascript:alert(1)', 'a.png', 'https://a b']) {
      expect(personaErrors({ ...valid, avatar_url: bad }).avatar_url, bad).toBeDefined();
    }
  });

  it('builds a body with exactly the PersonaIn contract keys', () => {
    const body = personaFromForm({ ...valid, name: '  Марина ' });
    expect(Object.keys(body).sort()).toEqual(Object.keys(EMPTY_PERSONA).sort());
    expect(body.name).toBe('Марина');
  });

  it('round-trips a saved persona', () => {
    const p: Persona = { ...EMPTY_PERSONA, id: 3, name: 'Анна', agent_prompt: 'Ты Анна.', created_at: '' };
    expect(personaFromForm(personaToForm(personaToInput(p)))).toEqual(personaToInput(p));
  });

  it('defaults to text-only replies without a voice', () => {
    expect(valid.voice_mode).toBe('off');
    expect(valid.voice_id).toBe('');
    expect(personaFromForm(valid)).toMatchObject({ voice_id: '', voice_mode: 'off', voice_style: '' });
  });

  it('voice notes are clean by default and offer room, street, cafe and random ambience', () => {
    expect(valid.voice_ambience).toBe('off');
    expect(personaFromForm({ ...valid, voice_ambience: 'street' }).voice_ambience).toBe('street');
    expect(VOICE_AMBIENCE_OPTIONS.map((o) => o.value)).toEqual(['off', 'room', 'street', 'cafe', 'random', 'contextual']);
  });

  it('requires a Gemini voice once voice replies are on', () => {
    expect(personaErrors({ ...valid, voice_mode: 'mirror' }).voice_id).toBe('Для голосовых ответов выберите голос');
    expect(personaErrors({ ...valid, voice_mode: 'always', voice_id: 'Kore' })).toEqual({});
    expect(personaErrors({ ...valid, voice_id: '21m00Tcm4TlvDq8ikWAM' }).voice_id).toBe('Выберите голос из списка');
    expect(personaErrors({ ...valid, voice_style: 'x'.repeat(301) }).voice_style).toBe('Не длиннее 300 символов');
  });

  it('phone calls are off by default, need a voice and round-trip to the API body', () => {
    expect(valid.accept_calls).toBe(false);
    expect(personaErrors({ ...valid, accept_calls: true }).voice_id).toBe('Для звонков выберите голос');
    expect(personaErrors({ ...valid, accept_calls: true, voice_id: 'Kore' })).toEqual({});
    expect(personaFromForm({ ...valid, accept_calls: true, voice_id: 'Kore' }).accept_calls).toBe(true);
  });

  it('offers Kore and Vindemiatrix, the voices the owner chose, and three voice modes', () => {
    expect(GEMINI_VOICES.map((v) => v.id)).toEqual(['Kore', 'Vindemiatrix']);
    expect(VOICE_MODES.map((m) => m.id)).toEqual(['off', 'mirror', 'always']);
    expect(VOICE_OPTIONS[0]).toEqual({ value: '', label: 'Без голоса' });
  });
});

describe('ageFromBirthDate', () => {
  const today = new Date(2026, 10, 14);   // 14 November 2026

  it('counts full years and waits for the birthday', () => {
    expect(ageFromBirthDate('1989-01-26', today)).toBe(37);
    expect(ageFromBirthDate('1989-11-14', today)).toBe(37);
    expect(ageFromBirthDate('1989-11-15', today)).toBe(36);
    expect(ageFromBirthDate('1989-12-01', today)).toBe(36);
  });

  it('is unknown without a date', () => {
    expect(ageFromBirthDate('', today)).toBeNull();
    expect(ageFromBirthDate('26.01.1989', today)).toBeNull();
  });
});
