import type { Persona } from '@/lib/models';
import {
  EMPTY_PERSONA, ROUTINE_MAX, personaErrors, personaFromForm, personaToForm, personaToInput, type PersonaFormValues,
} from './persona-schema';
import { GEMINI_VOICES, VOICE_AMBIENCE_OPTIONS, VOICE_MODES, VOICE_OPTIONS } from './persona-voices';

const valid: PersonaFormValues = personaToForm({ ...EMPTY_PERSONA, name: 'Марина', description: 'Тёплая, энергичная' });

describe('persona form validation', () => {
  it('is invalid when empty', () => {
    const errors = personaErrors(personaToForm(EMPTY_PERSONA));
    expect(errors.name).toBe('Укажите имя');
    expect(errors.description).toBe('Опишите персонажа');
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

  it('does not block on hidden fields: empty language and gender fall back to ru and female', () => {
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

  it('passes hidden prohibitions through unchanged', () => {
    expect(personaFromForm({ ...valid, prohibitions: 'старое правило' }).prohibitions).toBe('старое правило');
  });

  it('limits age to 18..100 but allows it to be empty', () => {
    expect(personaErrors({ ...valid, age: '17' }).age).toBe('18–100');
    expect(personaErrors({ ...valid, age: '101' }).age).toBe('18–100');
    expect(personaErrors({ ...valid, age: '45' }).age).toBeUndefined();
    expect(personaErrors({ ...valid, age: '' }).age).toBeUndefined();
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
    const body = personaFromForm({ ...valid, age: '30', name: '  Марина ' });
    expect(Object.keys(body).sort()).toEqual(Object.keys(EMPTY_PERSONA).sort());
    expect(body.age).toBe(30);
    expect(body.name).toBe('Марина');
    expect(personaFromForm(valid).age).toBeNull();
  });

  it('round-trips a saved persona', () => {
    const p: Persona = { ...EMPTY_PERSONA, id: 3, name: 'Анна', age: 28, description: 'd', created_at: '' };
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
    expect(VOICE_AMBIENCE_OPTIONS.map((o) => o.value)).toEqual(['off', 'room', 'street', 'cafe', 'random']);
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
