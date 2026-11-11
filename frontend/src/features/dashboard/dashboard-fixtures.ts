import type { AnalyticsSummary } from '@/lib/models';

/** Sample analytics reply shared by dashboard tests. */
export const summary: AnalyticsSummary = {
  contacts: 120,
  new_contacts: 40,
  replied: 75,
  by_stage: { first_contact: 50, engaged: 30, paid: 3 },
  registrations: 12,
  payments: 3,
  revenue: null,
  partner_payouts: null,
  revenue_by_currency: [{ currency: 'USD', total: 300 }],
  partner_payouts_by_currency: [],
  conversion: { contact_to_registration: 0.1, registration_to_payment: 0.25 },
  llm: { calls: 410, cost_usd: 1.5, tokens_in: 1, tokens_out: 1 },
  guard: { ok: 10, rewritten: 2, blocked: 1 },
  by_channel: [{ kind: 'whatsapp', contacts: 70, registrations: 8, payments: 2 }],
  daily: [{ date: '2026-10-08', contacts: 4, registrations: 1, payments: 0 }],
};
