import type { Strategy } from '@/lib/models';
import { PLAYBOOK_STAGES, parseHours, strategyErrors, strategyFromForm, strategyToForm } from './strategy-schema';

const ORIGINAL: Strategy = {
  id: 1, name: 'old', goal: '', offer: '', partner_name: '', registration_url: '', prompt: '',
  stage_playbook: { legacy_stage: 'keep me' }, rules: [], followup_hours: [], reply_delay_s: [1, 2], created_at: '',
};

describe('strategy form', () => {
  it('parses follow-up hours and drops garbage', () => {
    expect(parseHours('24, 72;168  x 0 -5')).toEqual([24, 72, 168]);
  });

  it('defaults a new strategy to 24/72/168 h and a 20–90 s delay, with a playbook field per AI stage', () => {
    const v = strategyToForm(null);
    expect(v.followup).toBe('24, 72, 168');
    expect([v.delay_min, v.delay_max]).toEqual(['20', '90']);
    expect(Object.keys(v.playbook)).toEqual(PLAYBOOK_STAGES.map((s) => s.id));
  });

  it('flags reply delay with min greater than max', () => {
    const v = { ...strategyToForm(null), name: 'S', delay_min: '100', delay_max: '10' };
    expect(strategyErrors(v)['delay_max']).toBe('Минимальная задержка не может быть больше максимальной.');
    expect(strategyErrors({ ...v, delay_min: '5' })).toEqual({});
  });

  it('requires a name, a valid URL and positive follow-up hours', () => {
    const errors = strategyErrors({ ...strategyToForm(null), registration_url: 'ftp://x', followup: '24, -1' });
    expect(errors['name']).toBe('Укажите название');
    expect(errors['registration_url']).toBeDefined();
    expect(errors['followup']).toBeDefined();
  });

  it('builds the API payload and keeps unknown playbook keys', () => {
    const v = strategyToForm(ORIGINAL);
    const body = strategyFromForm(
      {
        ...v,
        name: ' RCIPM ',
        followup: '24, 72',
        delay_min: '20',
        delay_max: '90',
        playbook: { ...v.playbook, engaged: 'Узнать о человеке' },
        rules: [{ text: ' не давить ' }, { text: '  ' }],
      },
      ORIGINAL,
    );
    expect(body.name).toBe('RCIPM');
    expect(body.prompt).toBe('');
    expect(body.followup_hours).toEqual([24, 72]);
    expect(body.reply_delay_s).toEqual([20, 90]);
    expect(body.rules).toEqual(['не давить']);
    expect(body.stage_playbook['legacy_stage']).toBe('keep me');
    expect(body.stage_playbook['engaged']).toBe('Узнать о человеке');
  });
});
