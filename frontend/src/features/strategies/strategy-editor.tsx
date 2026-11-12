import { zodResolver } from '@hookform/resolvers/zod';
import type { ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { Field, FormGrid } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type { Strategy, StrategyIn } from '@/lib/models';
import { strategyFromForm, strategySchema, strategyToForm, type StrategyFormValues } from './strategy-schema';

function Block({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3" aria-label={title}>
      <div>
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      {children}
    </section>
  );
}

/** Three blocks on one screen: what we sell, how to lead the talk, reminders. */
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
  const { register, handleSubmit, formState } = useForm<StrategyFormValues>({
    resolver: zodResolver(strategySchema),
    defaultValues: strategyToForm(strategy),
    mode: 'onTouched',
  });
  const errors = formState.errors;

  return (
    <form
      noValidate
      onSubmit={handleSubmit((v) => onSave(strategyFromForm(v, strategy)))}
      className="flex flex-col gap-6 rounded-xl border bg-card p-4 shadow-xs sm:p-5"
    >
      <Block title="Что продаём" hint="Партнёр, продукт и ссылка, по которой клиент регистрируется">
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
          <Field label="Оффер" hint="Что за продукт, цены, условия. Чего здесь нет, персонаж не выдумывает" className="sm:col-span-2">
            <Textarea {...register('offer')} className="min-h-24" />
          </Field>
        </FormGrid>
      </Block>

      <Block
        title="Как вести разговор"
        hint="Один текст: цель, шаги воронки, тон. Ориентиры, а не скрипт. Ограничения настраиваются в симуляторе"
      >
        <Field label="Сценарий">
          <Textarea {...register('prompt')} className="min-h-80 max-h-[70vh]" />
        </Field>
      </Block>

      <Block title="Напоминания" hint="Если клиент замолчал, персонаж напишет снова через столько дней">
        <Field label="Через сколько дней" hint="Например: 1, 3, 7" error={errors.followup_days?.message} className="sm:max-w-sm">
          <Input {...register('followup_days')} inputMode="decimal" />
        </Field>
      </Block>

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
