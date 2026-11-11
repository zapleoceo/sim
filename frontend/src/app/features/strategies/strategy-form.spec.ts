import { createStrategyForm, parseHours, strategyFromForm } from './strategy-form';

describe('strategy form', () => {
  it('parses follow-up hours and drops garbage', () => {
    expect(parseHours('24, 72;168  x 0 -5')).toEqual([24, 72, 168]);
  });

  it('has no artificial reply delay', () => {
    expect(Object.keys(createStrategyForm().controls)).not.toContain('delay_min');
  });

  it('builds the API payload and keeps unknown playbook keys', () => {
    const form = createStrategyForm();
    form.patchValue({ name: ' RCIPM ', followup: '24, 72' });
    form.controls.playbook.patchValue({ engaged: 'Узнать о человеке' });
    const body = strategyFromForm(form, {
      id: 1, name: 'old', goal: '', offer: '', partner_name: '', registration_url: '',
      prompt: '', stage_playbook: { legacy_stage: 'keep me' }, rules: [], followup_hours: [], created_at: '',
    });
    expect(body.name).toBe('RCIPM');
    expect(body.prompt).toBe('');
    expect(body.followup_hours).toEqual([24, 72]);
    expect('reply_delay_s' in body).toBeFalse();
    expect(body.stage_playbook['legacy_stage']).toBe('keep me');
    expect(body.stage_playbook['engaged']).toBe('Узнать о человеке');
  });
});
