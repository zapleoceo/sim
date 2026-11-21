import { Lock, LockOpen, RotateCcw, Save } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { InlineError } from '@/components/empty-state';
import { Field } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';
import { formatDateTime } from '@/lib/format';
import type { LeadStage, Passport, PassportField } from '@/lib/types';
import { useUpdateClient } from './api';
import {
  PASSPORT_FIELDS, STAGES, passportFormErrors, passportPatch, stageInfo, toPassportForm, unlockPatch, type PassportForm,
} from './passport-logic';

const FIELD_INPUT: Partial<Record<PassportField, { inputMode?: 'numeric'; placeholder?: string }>> = {
  age: { inputMode: 'numeric' },
  temperature: { inputMode: 'numeric', placeholder: '0–100' },
  timezone: { placeholder: 'Europe/Warsaw' },
};

function LockMark({ locked, label, onUnlock, busy }: { locked: boolean; label: string; onUnlock: () => void; busy: boolean }) {
  if (!locked) return null;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded px-1 text-[11px] font-normal text-muted-foreground hover:text-foreground disabled:opacity-50"
      onClick={onUnlock}
      disabled={busy}
      title="Поле изменено вручную: паспортистка его не трогает. Нажмите, чтобы снова разрешить."
      aria-label={`Снять замок: ${label}`}
    >
      <Lock className="size-3" />
      вручную
    </button>
  );
}

/**
 * The lead's passport (docs/leads.md): the living text and the structured fields. Fields the
 * operator changes are locked for the passport writer (a lock mark; click to unlock).
 */
export function PassportEditor({ clientId, passport }: { clientId: number; passport: Passport }) {
  const initial = useMemo(() => toPassportForm(passport), [passport]);
  const [form, setForm] = useState<PassportForm>(initial);
  const [errors, setErrors] = useState<string[]>([]);
  const update = useUpdateClient(clientId, (p) => (p.passport?.locked_fields ? 'Поле снова ведёт паспортистка' : 'Паспорт сохранён'));
  const changed = passportPatch(form, initial);
  const dirty = Object.keys(changed).length > 0;
  useUnsavedGuard(dirty);

  // A fresh passport from the server replaces the form unless the operator is editing it.
  useEffect(() => {
    if (!dirty) setForm(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  const locked = new Set(passport.locked_fields);
  const set = (key: keyof PassportForm, value: string): void => setForm((f) => ({ ...f, [key]: value }));
  const save = (): void => {
    const problems = passportFormErrors(form);
    setErrors(problems);
    if (problems.length === 0 && dirty) update.mutate({ passport: changed }, { onSuccess: () => setErrors([]) });
  };

  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      aria-label="Паспорт клиента"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={stageInfo(passport.stage).variant}>{stageInfo(passport.stage).label}</Badge>
        <span className="text-muted-foreground">теплота {passport.temperature}/100</span>
        <span className="ml-auto text-xs text-muted-foreground">
          {passport.updated_at ? `обновлён ${formatDateTime(passport.updated_at)}` : 'ещё не заполнен'}
        </span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
        {PASSPORT_FIELDS.map(({ field, label }) => {
          const id = `passport-${field}`;
          const lock = (
            <LockMark
              locked={locked.has(field)}
              label={label}
              busy={update.isPending}
              onUnlock={() => update.mutate({ passport: unlockPatch(passport.locked_fields, field) })}
            />
          );
          return (
            <Field key={field} id={id} label={<span className="flex items-center gap-1.5">{label}{lock}</span>}>
              {field === 'stage' ? (
                <NativeSelect id={id} value={form.stage} onChange={(e) => setForm((f) => ({ ...f, stage: e.target.value as LeadStage }))}>
                  {STAGES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </NativeSelect>
              ) : (
                <Input
                  id={id}
                  autoComplete="off"
                  {...(FIELD_INPUT[field] ?? {})}
                  value={form[field]}
                  onChange={(e) => set(field, e.target.value)}
                />
              )}
            </Field>
          );
        })}
      </div>
      <Field
        id="passport-text"
        label="Паспорт"
        hint="По разделам: кто он, жизнь, цели и боли, отношение к предложению, как общается, что она о себе рассказала, открытые вопросы."
      >
        <Textarea id="passport-text" className="max-h-[60vh] min-h-48 text-[13px]" value={form.text} onChange={(e) => set('text', e.target.value)} />
      </Field>
      {errors.length > 0 && <InlineError>{errors.join('. ')}</InlineError>}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {locked.size > 0 && (
          <span className="mr-auto flex items-center gap-1 text-xs text-muted-foreground">
            <LockOpen className="size-3" />
            Поля с замком паспортистка не меняет
          </span>
        )}
        <Button variant="ghost" disabled={!dirty || update.isPending} onClick={() => setForm(initial)}>
          <RotateCcw />
          Отменить
        </Button>
        <Button type="submit" disabled={!dirty || update.isPending}>
          <Save />
          Сохранить паспорт
        </Button>
      </div>
    </form>
  );
}
