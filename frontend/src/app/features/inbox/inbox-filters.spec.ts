import { DEFAULT_DIALOG_FILTER, activeFilterCount, createInboxFilterForm, normalizeFilter } from './inbox-filters';

describe('inbox filter state', () => {
  it('starts with defaults and zero active filters', () => {
    const form = createInboxFilterForm();
    expect(form.getRawValue()).toEqual(DEFAULT_DIALOG_FILTER);
    expect(activeFilterCount(form.getRawValue())).toBe(0);
  });

  it('counts every non-default filter', () => {
    const form = createInboxFilterForm();
    form.patchValue({
      channel_kind: 'telegram', stage: 'engaged', unread_only: true, needs_operator: true, persona_id: 3, q: 'анна',
    });
    expect(activeFilterCount(form.getRawValue())).toBe(6);
  });

  it('does not count a whitespace-only search', () => {
    const form = createInboxFilterForm();
    form.patchValue({ q: '   ' });
    expect(activeFilterCount(form.getRawValue())).toBe(0);
  });

  it('reset() returns to defaults', () => {
    const form = createInboxFilterForm();
    form.patchValue({ status: 'paused', q: 'x' });
    form.reset();
    expect(form.getRawValue()).toEqual(DEFAULT_DIALOG_FILTER);
  });

  it('emits through valueChanges for each edit', () => {
    const form = createInboxFilterForm();
    const seen: string[] = [];
    form.valueChanges.subscribe((v) => seen.push(v.channel_kind ?? ''));
    form.controls.channel_kind.setValue('whatsapp');
    form.controls.channel_kind.setValue('instagram');
    expect(seen).toEqual(['whatsapp', 'instagram']);
  });

  it('normalizeFilter trims the search text only', () => {
    const n = normalizeFilter({ ...DEFAULT_DIALOG_FILTER, q: '  мария ', stage: 'engaged' });
    expect(n.q).toBe('мария');
    expect(n.stage).toBe('engaged');
  });
});
