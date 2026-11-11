import { DEFAULT_CLIENT_FILTER, buildTimeline, clientApiFilter, clientFilterCount } from './client-logic';

describe('client timeline', () => {
  it('merges funnel and partner events newest first with readable titles', () => {
    const items = buildTimeline(
      [{ created_at: '2026-11-01T10:00:00Z', from_stage: 'first_contact', to_stage: 'engaged', actor: 'ai', reason: 'ответил' }],
      [
        { event: 'payment', amount: 100, currency: 'USD', payout: 20, occurred_at: '2026-11-03T10:00:00Z' },
        { event: 'registration', occurred_at: '2026-11-02T10:00:00Z' },
      ],
    );
    expect(items.map((i) => i.title)).toEqual([
      'Оплата у партнёра',
      'Регистрация у партнёра',
      'Этап: Первый контакт → Вовлечён',
    ]);
    expect(items[0]).toMatchObject({ icon: 'payment', detail: '100 USD · выплата 20', partner: true });
    expect(items[2]).toMatchObject({ icon: 'stage', detail: 'ai · ответил', partner: false });
  });

  it('falls back to the event type when there is no target stage', () => {
    expect(buildTimeline([{ created_at: '2026-11-01T10:00:00Z', type: 'note' }], [])[0].title).toBe('note');
  });
});

describe('clients filter', () => {
  it('counts active filters and trims the query', () => {
    expect(clientFilterCount(DEFAULT_CLIENT_FILTER)).toBe(0);
    expect(clientFilterCount({ q: ' a ', stage: 'paid', status: '', persona_id: 2 })).toBe(3);
    expect(clientApiFilter({ ...DEFAULT_CLIENT_FILTER, q: '  ann ' }).q).toBe('ann');
  });
});
