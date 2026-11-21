import { SwitchRow } from '@/components/field';
import { Segmented } from '@/components/segmented';
import { Switch } from '@/components/ui/switch';
import type { AttentionSettings } from '@/lib/types';
import { ATTENTION_MODES, type AttentionFlag, PACE, clampPace, modeHint, paceLabel, withFlag } from './attention-logic';

const SWITCHES: readonly { key: AttentionFlag; label: string; hint: string }[] = [
  { key: 'may_leave_read', label: 'Может оставить прочитанным', hint: 'Прочитать и не ответить на «ок» или спам.' },
  { key: 'after_missed_call', label: 'Писать после пропущенного звонка', hint: 'Не смогла взять трубку — напишет, когда освободится.' },
  { key: 'night_dnd', label: 'Ночью «не беспокоить»', hint: 'Во время сна из распорядка телефон не берёт, звонки пропущены.' },
];

/** «Внимание» settings inside its agent card (docs/attention.md): the mode, one pace slider and
 * three switches. Controlled: every change goes to the section's draft (one Save). */
export function AttentionBlock({ value, onChange }: { value: AttentionSettings; onChange: (next: AttentionSettings) => void }) {
  const set = (patch: Partial<AttentionSettings>) => onChange({ ...value, ...patch });
  const instant = value.mode === 'instant';
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-md border bg-muted/30 p-3">
      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="text-sm font-medium">Как она отвечает</span>
        <Segmented
          label="Внимание: режим"
          value={value.mode}
          options={ATTENTION_MODES.map((m) => ({ value: m.value, label: m.label, title: m.hint }))}
          onChange={(mode) => set({ mode })}
          className="h-auto w-full flex-wrap sm:w-fit"
        />
        <p className="text-xs text-muted-foreground">{modeHint(value.mode)}</p>
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <label htmlFor="attention-pace" className="flex items-center justify-between gap-2 text-sm font-medium">
          Темп
          <span className="text-xs font-normal text-muted-foreground tabular" aria-live="polite">{paceLabel(value.pace)}</span>
        </label>
        <input
          id="attention-pace"
          type="range"
          min={PACE.min}
          max={PACE.max}
          step={PACE.step}
          value={value.pace}
          disabled={instant}
          aria-valuetext={paceLabel(value.pace)}
          onChange={(e) => set({ pace: clampPace(Number(e.target.value)) })}
          className="w-full accent-primary disabled:opacity-50"
        />
        <div className="flex justify-between text-[11px] text-muted-foreground" aria-hidden="true">
          <span>быстро</span>
          <span>обычно</span>
          <span>неторопливо</span>
        </div>
        {instant && <p className="text-xs text-muted-foreground">В режиме «Всегда сразу» темп не действует.</p>}
      </div>

      <div className="flex flex-col divide-y">
        {SWITCHES.map((s) => (
          <SwitchRow key={s.key} id={`attention-${s.key}`} label={s.label} hint={s.hint}>
            <Switch
              id={`attention-${s.key}`}
              checked={value[s.key]}
              disabled={instant}
              onCheckedChange={(checked) => onChange(withFlag(value, s.key, checked))}
            />
          </SwitchRow>
        ))}
      </div>
    </div>
  );
}
