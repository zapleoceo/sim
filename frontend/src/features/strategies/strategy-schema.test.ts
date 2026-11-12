import type { Strategy } from '@/lib/models';
import { hoursToDays, parseDays, strategyErrors, strategyFromForm, strategyToForm } from './strategy-schema';

const ORIGINAL: Strategy = {
  id: 1, name: 'old', goal: 'старая цель', offer: '', partner_name: '', registration_url: '', prompt: 'текст',
  stage_playbook: { legacy_stage: 'keep me' }, rules: ['старое'], followup_hours: [24, 72, 168], created_at: '',
};

describe('strategy form', () => {
  it('shows reminders in days and parses them back', () => {
    expect(hoursToDays([24, 72, 168])).toBe('1, 3, 7');
    expect(hoursToDays([12])).toBe('0.5');
    expect(parseDays('1, 3;7  x 0 -5')).toEqual([1, 3, 7]);
  });

  it('defaults a new strategy to reminders after 1, 3 and 7 days', () => {
    expect(strategyToForm(null).followup_days).toBe('1, 3, 7');
  });

  it('requires a name, a valid URL and ascending positive days', () => {
    const errors = strategyErrors({ ...strategyToForm(null), registration_url: 'ftp://x', followup_days: '3, 1' });
    expect(errors['name']).toBe('Укажите название');
    expect(errors['registration_url']).toBeDefined();
    expect(errors['followup_days']).toBe('Положительные числа по возрастанию, через запятую');
  });

  it('builds the API payload: days to hours, old fields passed through unchanged', () => {
    const body = strategyFromForm({ ...strategyToForm(ORIGINAL), name: ' RCIPM ', followup_days: '1, 2.5' }, ORIGINAL);
    expect(body.name).toBe('RCIPM');
    expect(body.prompt).toBe('текст');
    expect(body.followup_hours).toEqual([24, 60]);
    expect(body.goal).toBe('старая цель');
    expect(body.rules).toEqual(['старое']);
    expect(body.stage_playbook).toEqual({ legacy_stage: 'keep me' });
    expect(body).not.toHaveProperty('reply_delay_s');
  });

  it('has only the three blocks of fields', () => {
    expect(Object.keys(strategyToForm(null)).sort()).toEqual(
      ['followup_days', 'name', 'offer', 'partner_name', 'prompt', 'registration_url'],
    );
  });
});
