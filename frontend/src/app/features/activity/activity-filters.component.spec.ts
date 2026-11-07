import { createActivityForm } from './activity-filters.component';

describe('activity filters validation', () => {
  it('rejects a reversed period and invalid identifiers, retains explicit false outcome', () => {
    const form = createActivityForm();
    form.patchValue({ from: '2026-11-07', to: '2026-11-01' });
    expect(form.hasError('dateOrder')).toBeTrue();
    form.patchValue({ to: '2026-11-07', channel_id: -1 });
    expect(form.invalid).toBeTrue();
    form.patchValue({ channel_id: 1.5 }); expect(form.invalid).toBeTrue();
    form.patchValue({ channel_id: 4, has_error: false });
    expect(form.valid).toBeTrue(); expect(form.getRawValue().has_error).toBeFalse();
    form.reset(); expect(form.getRawValue().has_error).toBeNull(); expect(form.getRawValue().from).toBe('');
  });
});
