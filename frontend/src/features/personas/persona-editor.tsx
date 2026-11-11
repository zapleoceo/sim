import { zodResolver } from '@hookform/resolvers/zod';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { Avatar } from '@/components/avatar';
import { Field, FormGrid } from '@/components/field';
import type { Option } from '@/components/option-select';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { BrokerModel, Persona, PersonaIn, Strategy } from '@/lib/models';
import { SelectControl } from './select-control';
import { VOICE_MODE_OPTIONS, VOICE_OPTIONS } from './persona-voices';
import {
  EMPTY_PERSONA, GENDERS, countRules, personaFromForm, personaSchema, personaToForm, personaToInput, type PersonaFormValues,
} from './persona-schema';

/** Model options: «Авто» first, Broker models (unavailable ones disabled), and the saved value if Broker no longer lists it. */
export function modelOptions(models: readonly BrokerModel[], current: string): Option<string>[] {
  const out: Option<string>[] = [
    { value: '', label: 'Авто — цепочка chat:sales' },
    ...models.map((m) => ({
      value: m.id,
      disabled: !m.available,
      label: (
        <span className="flex items-center gap-2">
          {m.name}
          {m.subscription && <Badge variant="muted">подписка</Badge>}
        </span>
      ),
    })),
  ];
  if (current && !models.some((m) => m.id === current)) out.push({ value: current, label: current });
  return out;
}

export function PersonaEditor({
  persona,
  models,
  modelsUnavailable,
  strategies,
  saving,
  onSave,
  onRemove,
}: {
  /** `null` — a new persona. */
  persona: Persona | null;
  models: BrokerModel[];
  modelsUnavailable: boolean;
  strategies: Strategy[];
  saving: boolean;
  onSave: (body: PersonaIn) => void;
  onRemove?: () => void;
}) {
  const form = useForm<PersonaFormValues>({
    resolver: zodResolver(personaSchema),
    defaultValues: personaToForm(persona ? personaToInput(persona) : EMPTY_PERSONA),
    mode: 'onTouched',
  });
  const { register, control, handleSubmit, formState } = form;
  const errors = formState.errors;
  const [prohibitions, model, name, avatarUrl] = useWatch({ control, name: ['prohibitions', 'model', 'name', 'avatar_url'] });

  const strategyOptions: Option<number | null>[] = [
    { value: null, label: 'Не выбрана' },
    ...strategies.map((s) => ({ value: s.id, label: s.name })),
  ];

  return (
    <form
      noValidate
      onSubmit={handleSubmit((v) => onSave(personaFromForm(v)))}
      className="flex flex-col gap-5 rounded-xl border bg-card p-4 shadow-xs sm:p-5"
    >
      <div className="flex items-center gap-3">
        <Avatar name={name || '?'} url={avatarUrl} size={52} />
        <div className="flex items-center gap-2">
          <Controller
            control={control}
            name="is_active"
            render={({ field }) => (
              <Switch id="persona-active" checked={field.value} onCheckedChange={field.onChange} />
            )}
          />
          <Label htmlFor="persona-active">Активен</Label>
        </div>
      </div>

      <FormGrid className="lg:grid-cols-3">
        <Field label="Имя" error={errors.name?.message} className="sm:col-span-2 lg:col-span-1">
          <Input {...register('name')} autoComplete="off" />
        </Field>
        <Field label="Возраст" error={errors.age?.message}>
          <Input {...register('age')} inputMode="numeric" />
        </Field>
        <Field label="Пол" error={errors.gender?.message}>
          <SelectControl control={control} name="gender" options={GENDERS} />
        </Field>
        <Field label="Язык" error={errors.language?.message}>
          <Input {...register('language')} placeholder="ru" />
        </Field>
        <Field label="Модель" hint={modelsUnavailable ? 'Список моделей недоступен — используется авто-цепочка' : undefined}>
          <SelectControl control={control} name="model" options={modelOptions(models, model)} />
        </Field>
        <Field label="Стратегия">
          <SelectControl control={control} name="strategy_id" options={strategyOptions} />
        </Field>
      </FormGrid>

      <Field label="Ссылка на аватар" error={errors.avatar_url?.message}>
        <Input {...register('avatar_url')} placeholder="https://…" inputMode="url" />
      </Field>

      <Field label="Описание персоны" error={errors.description?.message}>
        <Textarea
          {...register('description')}
          className="min-h-72 max-h-[70vh]"
          placeholder="Кто она: имя, возраст, профессия, откуда. Характер и манера держаться. Короткая биография. Как пишет: длина сообщений, «вы»/«ты», эмодзи, любимые обороты. Всё одним текстом, можно с заголовками и списками."
        />
      </Field>

      <Field
        label="Чего нельзя"
        hint="Одно правило на строку. Этим же руководствуется контролер ответов."
        error={errors.prohibitions?.message}
        counter={`Правил: ${countRules(prohibitions ?? '')}`}
      >
        <Textarea
          {...register('prohibitions')}
          className="min-h-32 max-h-[50vh]"
          placeholder={'Одно правило на строку:\nне обещать доход и гарантии\nне просить пароли и данные карт'}
        />
      </Field>

      <FormGrid className="lg:grid-cols-2">
        <Field label="Голосовые ответы" hint="Голос синтезирует Google Gemini TTS через AIbroker; текст ответа остаётся в диалоге">
          <SelectControl control={control} name="voice_mode" options={VOICE_MODE_OPTIONS} />
        </Field>
        <Field label="Голос (Google Gemini)" error={errors.voice_id?.message}>
          <SelectControl control={control} name="voice_id" options={VOICE_OPTIONS} />
        </Field>
      </FormGrid>

      <Field label="Манера речи (необязательно)" hint="Подсказка для синтеза: как произносить, а не что" error={errors.voice_style?.message}>
        <Input {...register('voice_style')} placeholder="тепло, спокойно, с улыбкой" autoComplete="off" />
      </Field>

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
