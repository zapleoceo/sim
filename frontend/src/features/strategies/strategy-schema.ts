import { z } from 'zod';
import type { Strategy, StrategyIn } from '@/lib/models';
import { STAGES } from '@/lib/stages';

/** Stages with an AI playbook (AI reaches at most handed_to_partner). */
export const PLAYBOOK_STAGES = STAGES.filter((s) => s.aiReachable);

/** «24, 72, 168» -> [24, 72, 168]; garbage is dropped. */
export function parseHours(value: string): number[] {
  return value
    .split(/[\s,;]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n) && n > 0);
}

function validHours(value: string): boolean {
  return value
    .split(/[\s,;]+/)
    .filter(Boolean)
    .every((p) => Number.isFinite(Number(p)) && Number(p) > 0);
}

export const strategySchema = z.object({
    name: z
      .string()
      .refine((v) => v.trim() !== '', 'Укажите название')
      .refine((v) => v.length <= 120, 'Не длиннее 120 символов'),
    goal: z.string(),
    prompt: z.string(),
    offer: z.string(),
    partner_name: z.string(),
    registration_url: z.string().refine((v) => /^(https?:\/\/\S+)?$/i.test(v.trim()), 'Нужна ссылка вида https://…'),
    playbook: z.record(z.string(), z.string()),
    rules: z.array(z.object({ text: z.string() })),
    followup: z.string().refine(validHours, 'Только положительные числа через запятую'),
  });

export type StrategyFormValues = z.infer<typeof strategySchema>;

export function strategyToForm(s: Strategy | null): StrategyFormValues {
  const playbook = Object.fromEntries(PLAYBOOK_STAGES.map((st) => [st.id, s?.stage_playbook?.[st.id] ?? '']));
  return {
    name: s?.name ?? '',
    goal: s?.goal ?? '',
    prompt: s?.prompt ?? '',
    offer: s?.offer ?? '',
    partner_name: s?.partner_name ?? '',
    registration_url: s?.registration_url ?? '',
    playbook,
    rules: (s?.rules ?? []).map((text) => ({ text })),
    followup: (s?.followup_hours ?? [24, 72, 168]).join(', '),
  };
}

export function strategyFromForm(v: StrategyFormValues, original: Strategy | null): StrategyIn {
  return {
    name: v.name.trim(),
    goal: v.goal,
    prompt: v.prompt,
    offer: v.offer,
    partner_name: v.partner_name,
    registration_url: v.registration_url.trim(),
    // keep stage keys the form does not show
    stage_playbook: { ...(original?.stage_playbook ?? {}), ...v.playbook },
    rules: v.rules.map((r) => r.text.trim()).filter(Boolean),
    followup_hours: parseHours(v.followup),
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
