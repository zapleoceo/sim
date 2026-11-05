import { AbstractControl, FormArray, FormControl, FormGroup, ValidationErrors, Validators } from '@angular/forms';
import { Strategy, StrategyIn } from '../../core/models';
import { STAGES } from '../../core/stages';

/** Этапы, для которых у ИИ есть плейбук (ИИ доходит максимум до handed_to_partner). */
export const PLAYBOOK_STAGES = STAGES.filter((s) => s.aiReachable);

export type StrategyForm = FormGroup<{
  name: FormControl<string>;
  goal: FormControl<string>;
  offer: FormControl<string>;
  partner_name: FormControl<string>;
  registration_url: FormControl<string>;
  playbook: FormGroup<Record<string, FormControl<string>>>;
  rules: FormArray<FormControl<string>>;
  followup: FormControl<string>;
  delay_min: FormControl<number>;
  delay_max: FormControl<number>;
}>;

/** «24, 72, 168» → [24, 72, 168]; мусор отбрасывается. */
export function parseHours(value: string): number[] {
  return value
    .split(/[\s,;]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n) && n > 0);
}

function hoursValidator(c: AbstractControl<string>): ValidationErrors | null {
  const parts = c.value.split(/[\s,;]+/).filter(Boolean);
  return parts.every((p) => Number.isFinite(Number(p)) && Number(p) > 0) ? null : { hours: true };
}

function delayValidator(g: AbstractControl): ValidationErrors | null {
  const min = g.get('delay_min')?.value as number;
  const max = g.get('delay_max')?.value as number;
  return min <= max ? null : { delayRange: true };
}

export function createStrategyForm(): StrategyForm {
  const playbook = Object.fromEntries(PLAYBOOK_STAGES.map((s) => [s.id, new FormControl('', { nonNullable: true })]));
  return new FormGroup(
    {
      name: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(120)] }),
      goal: new FormControl('', { nonNullable: true }),
      offer: new FormControl('', { nonNullable: true }),
      partner_name: new FormControl('', { nonNullable: true }),
      registration_url: new FormControl('', { nonNullable: true, validators: [Validators.pattern(/^(https?:\/\/\S+)?$/i)] }),
      playbook: new FormGroup(playbook),
      rules: new FormArray<FormControl<string>>([]),
      followup: new FormControl('24, 72, 168', { nonNullable: true, validators: [hoursValidator] }),
      delay_min: new FormControl(20, { nonNullable: true, validators: [Validators.required, Validators.min(0)] }),
      delay_max: new FormControl(90, { nonNullable: true, validators: [Validators.required, Validators.min(0)] }),
    },
    { validators: [delayValidator] },
  );
}

export function fillStrategyForm(form: StrategyForm, s: Strategy | null): void {
  form.reset();
  form.controls.rules.clear();
  for (const r of s?.rules ?? []) form.controls.rules.push(new FormControl(r, { nonNullable: true }));
  form.patchValue({
    name: s?.name ?? '',
    goal: s?.goal ?? '',
    offer: s?.offer ?? '',
    partner_name: s?.partner_name ?? '',
    registration_url: s?.registration_url ?? '',
    playbook: s?.stage_playbook ?? {},
    followup: (s?.followup_hours ?? [24, 72, 168]).join(', '),
    delay_min: s?.reply_delay_s[0] ?? 20,
    delay_max: s?.reply_delay_s[1] ?? 90,
  });
}

export function strategyFromForm(form: StrategyForm, original: Strategy | null): StrategyIn {
  const v = form.getRawValue();
  return {
    name: v.name.trim(),
    goal: v.goal,
    offer: v.offer,
    partner_name: v.partner_name,
    registration_url: v.registration_url.trim(),
    // сохраняем ключи, которых нет в форме
    stage_playbook: { ...(original?.stage_playbook ?? {}), ...v.playbook },
    rules: v.rules.map((r) => r.trim()).filter(Boolean),
    followup_hours: parseHours(v.followup),
    reply_delay_s: [v.delay_min, v.delay_max],
  };
}
