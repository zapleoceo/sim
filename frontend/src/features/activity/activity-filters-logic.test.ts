import { EMPTY_ACTIVITY_FORM, activityFilterCount, toActivityFilter, validateActivityForm } from './activity-filters-logic';

describe('activity filters', () => {
  it('rejects a reversed date range and a bad client id', () => {
    expect(validateActivityForm({ ...EMPTY_ACTIVITY_FORM, from: '2026-11-10', to: '2026-11-01' }).dateOrder).toBeTruthy();
    expect(validateActivityForm({ ...EMPTY_ACTIVITY_FORM, client_id: '0' }).client_id).toBeTruthy();
    expect(validateActivityForm({ ...EMPTY_ACTIVITY_FORM, client_id: '1.5' }).client_id).toBeTruthy();
    expect(validateActivityForm({ ...EMPTY_ACTIVITY_FORM, q: 'x'.repeat(201) }).q).toBeTruthy();
    expect(validateActivityForm({ ...EMPTY_ACTIVITY_FORM, from: '2026-11-01', to: '2026-11-01' })).toEqual({});
  });

  it('serializes only the fields of the chosen feed', () => {
    const form = { ...EMPTY_ACTIVITY_FORM, q: ' hi ', author: 'ai' as const, model: 'gpt', client_id: '5' };
    expect(toActivityFilter(form, { llm: false, clientScoped: false })).toEqual({
      q: 'hi', channel_kind: '', channel_id: null, persona_id: null, client_id: 5, from: null, to: null,
      direction: '', author: 'ai', delivery: '',
    });
    const llm = toActivityFilter({ ...form, has_error: false }, { llm: true, clientScoped: true });
    expect(llm).toMatchObject({ model: 'gpt', has_error: false, client_id: null });
    expect(llm).not.toHaveProperty('author');
  });

  it('returns null for an invalid form', () => {
    expect(toActivityFilter({ ...EMPTY_ACTIVITY_FORM, client_id: 'abc' }, { llm: false, clientScoped: false })).toBeNull();
  });

  it('counts filled filters', () => {
    expect(activityFilterCount(EMPTY_ACTIVITY_FORM)).toBe(0);
    expect(activityFilterCount({ ...EMPTY_ACTIVITY_FORM, has_error: false, channel_id: 3 })).toBe(2);
  });
});
