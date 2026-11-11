import { z } from 'zod';
import type { Strategy, StrategyIn } from '@/lib/models';

const HOURS_PER_DAY = 24;

/** «1, 3, 7» -> [1, 3, 7]; garbage is dropped. */
export function parseDays(value: string): number[] {
  return value
    .split(/[\s,;]+/)
    .filter(Boolean)
    .map((p) => Number(p.replace(',', '.')))
    .filter((n) => Number.isFinite(n) && n > 0);
}

function validDays(value: string): boolean {
  const parts = value.split(/[\s,;]+/).filter(Boolean);
  return parts.every((p) => Number.isFinite(Number(p)) && Number(p) > 0)
    && parts.map(Number).every((n, i, all) => i === 0 || n > all[i - 1]);
}

/** Hours from the API -> «1, 3, 7» days for people (fractions kept, e.g. 0.5). */
export function hoursToDays(hours: readonly number[]): string {
  return hours.map((h) => String(Math.round((h / HOURS_PER_DAY) * 100) / 100)).join(', ');
}

/**
 * Strategy form: three blocks. What we sell (name, partner, offer, registration link), how to lead
 * the talk (one text: goal, steps, guidance) and follow-up reminders in days. The old goal / per-stage
 * hints / rule list were merged into the text (migration 0012); their values pass through unchanged.
 */
export const strategySchema = z.object({
  name: z
    .string()
    .refine((v) => v.trim() !== '', 'Укажите название')
    .refine((v) => v.length <= 120, 'Не длиннее 120 символов'),
  partner_name: z.string(),
  offer: z.string(),
  registration_url: z.string().refine((v) => /^(https?:\/\/\S+)?$/i.test(v.trim()), 'Нужна ссылка вида https://…'),
  prompt: z.string(),
  followup_days: z.string().refine(validDays, 'Положительные числа по возрастанию, через запятую'),
});

export type StrategyFormValues = z.infer<typeof strategySchema>;

export function strategyToForm(s: Strategy | null): StrategyFormValues {
  return {
    name: s?.name ?? '',
    partner_name: s?.partner_name ?? '',
    offer: s?.offer ?? '',
    registration_url: s?.registration_url ?? '',
    prompt: s?.prompt ?? '',
    followup_days: hoursToDays(s?.followup_hours ?? [24, 72, 168]),
  };
}

export function strategyFromForm(v: StrategyFormValues, original: Strategy | null): StrategyIn {
  return {
    name: v.name.trim(),
    partner_name: v.partner_name,
    offer: v.offer,
    registration_url: v.registration_url.trim(),
    prompt: v.prompt,
    followup_hours: parseDays(v.followup_days).map((d) => Math.round(d * HOURS_PER_DAY * 100) / 100),
    // not edited here any more: kept as they are
    goal: original?.goal ?? '',
    stage_playbook: original?.stage_playbook ?? {},
    rules: original?.rules ?? [],
  };
}

/** Field -> first error message; empty when valid. */
export function strategyErrors(values: StrategyFormValues): Record<string, string> {
  const r = strategySchema.safeParse(values);
  if (r.success) return {};
  const out: Record<string, string> = {};
  for (const issue of r.error.issues) out[issue.path.join('.')] ??= issue.message;
  return out;
}
