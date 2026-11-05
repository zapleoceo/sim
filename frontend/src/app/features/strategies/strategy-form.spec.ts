import { createStrategyForm, parseHours, strategyFromForm } from './strategy-form';

describe('strategy form', () => {
  it('parses follow-up hours and drops garbage', () => {
    expect(parseHours('24, 72;168  x 0 -5')).toEqual([24, 72, 168]);
  });

  it('flags reply delay with min greater than max', () => {
    const form = createStrategyForm();
    form.patchValue({ name: 'S', delay_min: 100, delay_max: 10 });
    expect(form.hasError('delayRange')).toBeTrue();
    form.patchValue({ delay_min: 5 });
    expect(form.valid).toBeTrue();
  });

  it('builds the API payload and keeps unknown playbook keys', () => {
    const form = createStrategyForm();
    form.patchValue({ name: ' RCIPM ', followup: '24, 72', delay_min: 20, delay_max: 90 });
    form.controls.playbook.patchValue({ engaged: 'Узнать о человеке' });
    const body = strategyFromForm(form, {
      id: 1, name: 'old', goal: '', offer: '', partner_name: '', registration_url: '',
      stage_playbook: { legacy_stage: 'keep me' }, rules: [], followup_hours: [], reply_delay_s: [1, 2], created_at: '',
    });
    expect(body.name).toBe('RCIPM');
    expect(body.followup_hours).toEqual([24, 72]);
    expect(body.reply_delay_s).toEqual([20, 90]);
    expect(body.stage_playbook['legacy_stage']).toBe('keep me');
    expect(body.stage_playbook['engaged']).toBe('Узнать о человеке');
  });
});
