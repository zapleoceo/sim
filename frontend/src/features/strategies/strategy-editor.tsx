import { zodResolver } from '@hookform/resolvers/zod';
import { Plus, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { useFieldArray, useForm } from 'react-hook-form';
import { Field, FormGrid } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type { Strategy, StrategyIn } from '@/lib/models';
import { PLAYBOOK_STAGES, strategyFromForm, strategySchema, strategyToForm, type StrategyFormValues } from './strategy-schema';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

export function StrategyEditor({
  strategy,
  saving,
  onSave,
  onRemove,
}: {
  strategy: Strategy | null;
  saving: boolean;
  onSave: (body: StrategyIn) => void;
  onRemove?: () => void;
}) {
  const { register, control, handleSubmit, formState } = useForm<StrategyFormValues>({
    resolver: zodResolver(strategySchema),
    defaultValues: strategyToForm(strategy),
    mode: 'onTouched',
  });
  const rules = useFieldArray({ control, name: 'rules' });
  const errors = formState.errors;

  return (
    <form
      noValidate
      onSubmit={handleSubmit((v) => onSave(strategyFromForm(v, strategy)))}
      className="flex flex-col gap-6 rounded-xl border bg-card p-4 shadow-xs sm:p-5"
    >
      <FormGrid>
        <Field label="Название" error={errors.name?.message}>
          <Input {...register('name')} autoComplete="off" />
        </Field>
        <Field label="Партнёр">
          <Input {...register('partner_name')} />
        </Field>
        <Field
          label="Ссылка регистрации"
          hint="ИИ добавит к ней ?ref=<код клиента>"
          error={errors.registration_url?.message}
          className="sm:col-span-2"
        >
          <Input {...register('registration_url')} placeholder="https://…" inputMode="url" />
        </Field>
        <Field label="Цель" hint="Короткая формула: чего персонаж хочет добиться" className="sm:col-span-2">
          <Textarea {...register('goal')} className="min-h-20" />
        </Field>
        <Field
          label="Промпт стратегии"
          hint="Свободный текст: как вести разговор. Ориентиры, а не скрипт; подставляется в системный промпт"
          className="sm:col-span-2"
        >
          <Textarea {...register('prompt')} className="min-h-72 max-h-[70vh]" />
        </Field>
        <Field label="Оффер" className="sm:col-span-2">
          <Textarea {...register('offer')} className="min-h-24" />
        </Field>
      </FormGrid>

      <Section title="Playbook по этапам">
        {/* All stage prompts stay open: the owner wants to see every prompt without extra clicks. */}
        <div className="flex flex-col gap-4">
          {PLAYBOOK_STAGES.map((s) => (
            <Field
              key={s.id}
              label={
                <span className="flex items-center gap-2">
                  <span className="size-2 rounded-full" style={{ background: s.color }} />
                  {s.label}: что делает персонаж
                </span>
              }
            >
              <Textarea {...register(`playbook.${s.id}`)} className="min-h-24" />
            </Field>
          ))}
        </div>
      </Section>

      <Section title="Правила">
        <div className="flex flex-col gap-2">
          {rules.fields.map((f, i) => (
            <div key={f.id} className="flex items-center gap-2">
              <Input {...register(`rules.${i}.text`)} aria-label={`Правило ${i + 1}`} placeholder={`Правило ${i + 1}`} />
              <Button variant="ghost" size="icon" aria-label={`Удалить правило ${i + 1}`} onClick={() => rules.remove(i)}>
                <X />
              </Button>
            </div>
          ))}
          <Button variant="ghost" className="w-fit" onClick={() => rules.append({ text: '' })}>
            <Plus />
            Добавить правило
          </Button>
        </div>
      </Section>

      <Section title="Тайминги">
        <FormGrid className="sm:grid-cols-3">
          <Field
            label="Фоллоу-апы, часы"
            hint="Через сколько часов без ответа писать снова: 24, 72, 168"
            error={errors.followup?.message}
          >
            <Input {...register('followup')} />
          </Field>
          <Field label="Задержка ответа от, с" error={errors.delay_min?.message}>
            <Input {...register('delay_min')} inputMode="numeric" />
          </Field>
          <Field label="до, с" error={errors.delay_max?.message}>
            <Input {...register('delay_max')} inputMode="numeric" />
          </Field>
        </FormGrid>
      </Section>

      <div className="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] -mx-4 -mb-4 flex items-center gap-2 rounded-b-xl border-t bg-card/95 px-4 py-3 backdrop-blur sm:-mx-5 sm:-mb-5 sm:px-5 md:bottom-0">
        {onRemove && (
          <Button variant="ghost-destructive" onClick={onRemove}>
            Удалить
          </Button>
        )}
        <span className="flex-1" />
        <Button type="submit" disabled={saving}>
          {saving ? 'Сохраняем…' : 'Сохранить'}
        </Button>
      </div>
    </form>
  );
}
