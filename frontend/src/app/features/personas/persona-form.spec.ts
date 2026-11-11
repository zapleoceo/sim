import { EMPTY_PERSONA, countRules, createPersonaForm } from './persona-form';
import { GEMINI_VOICES, VOICE_MODES } from './persona-voices';

describe('persona form validation', () => {
  const valid = { ...EMPTY_PERSONA, name: 'Марина', description: 'Тёплая, энергичная' };

  it('is invalid when empty', () => {
    const form = createPersonaForm();
    expect(form.invalid).toBeTrue();
    expect(form.controls.name.hasError('required')).toBeTrue();
    expect(form.controls.description.hasError('required')).toBeTrue();
  });

  it('accepts a descriptive language up to the API limit', () => {
    const form = createPersonaForm(valid);
    form.controls.language.setValue('русский (переходит на украинский, если клиент пишет по-украински)');
    expect(form.controls.language.valid).toBeTrue();
    form.controls.language.setValue('x'.repeat(201));
    expect(form.controls.language.hasError('maxlength')).toBeTrue();
  });

  it('has no "are you a bot" field any more', () => {
    expect(Object.keys(createPersonaForm(valid).controls)).not.toContain('disclosure_text');
  });

  it('is valid with the required fields filled', () => {
    expect(createPersonaForm(valid).valid).toBeTrue();
  });

  it('requires the language and rejects an over-long name', () => {
    const form = createPersonaForm(valid);
    form.controls.language.setValue('');
    expect(form.controls.language.hasError('required')).toBeTrue();
    form.controls.language.setValue('ru');
    form.controls.name.setValue('x'.repeat(81));
    expect(form.controls.name.hasError('maxlength')).toBeTrue();
  });

  it('limits age to 18..100 but allows it to be empty', () => {
    const form = createPersonaForm(valid);
    form.controls.age.setValue(17);
    expect(form.controls.age.invalid).toBeTrue();
    form.controls.age.setValue(101);
    expect(form.controls.age.invalid).toBeTrue();
    form.controls.age.setValue(45);
    expect(form.controls.age.valid).toBeTrue();
    form.controls.age.setValue(null);
    expect(form.controls.age.valid).toBeTrue();
  });

  it('accepts http(s) and root-relative avatar URLs only', () => {
    const form = createPersonaForm(valid);
    for (const ok of ['', 'https://cdn.example.com/a.png', '/media/a.png']) {
      form.controls.avatar_url.setValue(ok);
      expect(form.controls.avatar_url.valid).withContext(ok).toBeTrue();
    }
    for (const bad of ['javascript:alert(1)', 'a.png', 'https://a b']) {
      form.controls.avatar_url.setValue(bad);
      expect(form.controls.avatar_url.hasError('pattern')).withContext(bad).toBeTrue();
    }
  });

  it('getRawValue matches the PersonaIn contract keys', () => {
    expect(Object.keys(createPersonaForm(valid).getRawValue()).sort()).toEqual(Object.keys(EMPTY_PERSONA).sort());
  });

  it('defaults to text-only replies without a voice', () => {
    const form = createPersonaForm(valid);
    expect(form.controls.voice_mode.value).toBe('off');
    expect(form.controls.voice_id.value).toBe('');
    expect(form.valid).toBeTrue();
  });

  it('requires a Gemini voice once voice replies are on', () => {
    const form = createPersonaForm(valid);
    form.controls.voice_mode.setValue('mirror');
    expect(form.hasError('voiceRequired')).toBeTrue();
    expect(form.invalid).toBeTrue();
    form.controls.voice_id.setValue('Kore');
    expect(form.valid).toBeTrue();
    form.controls.voice_style.setValue('x'.repeat(301));
    expect(form.controls.voice_style.hasError('maxlength')).toBeTrue();
  });

  it('offers the 30 Gemini voices and three voice modes', () => {
    expect(GEMINI_VOICES.length).toBe(30);
    expect(new Set(GEMINI_VOICES.map((v) => v.id)).size).toBe(30);
    expect(VOICE_MODES.map((m) => m.id)).toEqual(['off', 'mirror', 'always']);
  });

  it('counts only non-empty prohibition lines', () => {
    expect(countRules('')).toBe(0);
    expect(countRules(['не обещать доход', '', '  ', 'не просить пароли', ''].join('\n'))).toBe(2);
  });
});
