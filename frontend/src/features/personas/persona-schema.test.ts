import type { Persona } from '@/lib/models';
import {
  EMPTY_PERSONA, personaErrors, personaFromForm, personaToForm, personaToInput, type PersonaFormValues,
} from './persona-schema';
import { GEMINI_VOICES, VOICE_MODES, VOICE_OPTIONS } from './persona-voices';

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

  it('requires a Gemini voice once voice replies are on', () => {
    expect(personaErrors({ ...valid, voice_mode: 'mirror' }).voice_id).toBe('Для голосовых ответов выберите голос');
    expect(personaErrors({ ...valid, voice_mode: 'always', voice_id: 'Kore' })).toEqual({});
    expect(personaErrors({ ...valid, voice_id: '21m00Tcm4TlvDq8ikWAM' }).voice_id).toBe('Выберите голос из списка');
    expect(personaErrors({ ...valid, voice_style: 'x'.repeat(301) }).voice_style).toBe('Не длиннее 300 символов');
  });

  it('offers the 30 Gemini voices and three voice modes', () => {
    expect(GEMINI_VOICES).toHaveLength(30);
    expect(new Set(GEMINI_VOICES.map((v) => v.id)).size).toBe(30);
    expect(VOICE_MODES.map((m) => m.id)).toEqual(['off', 'mirror', 'always']);
    expect(VOICE_OPTIONS[0]).toEqual({ value: '', label: 'Без голоса' });
  });
});
