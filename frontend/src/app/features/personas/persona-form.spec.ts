import { EMPTY_PERSONA, countRules, createPersonaForm } from './persona-form';

describe('persona form validation', () => {
  const valid = { ...EMPTY_PERSONA, name: 'Марина', description: 'Тёплая, энергичная', disclosure_text: 'Я виртуальный ассистент проекта.' };

  it('is invalid when empty', () => {
    const form = createPersonaForm();
    expect(form.invalid).toBeTrue();
    expect(form.controls.name.hasError('required')).toBeTrue();
    expect(form.controls.description.hasError('required')).toBeTrue();
    expect(form.controls.disclosure_text.hasError('required')).toBeTrue();
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

  it('counts only non-empty prohibition lines', () => {
    expect(countRules('')).toBe(0);
    expect(countRules(['не обещать доход', '', '  ', 'не просить пароли', ''].join('\n'))).toBe(2);
  });
});
