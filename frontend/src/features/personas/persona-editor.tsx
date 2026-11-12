import { zodResolver } from '@hookform/resolvers/zod';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { Avatar } from '@/components/avatar';
import { Field, FormGrid } from '@/components/field';
import { modelOptions } from '@/components/model-options';
import type { Option } from '@/components/option-select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { BrokerModel, Persona, PersonaIn, Strategy } from '@/lib/models';
import { SelectControl } from './select-control';
import { PersonaFullPrompt } from './persona-full-prompt';
import { VOICE_AMBIENCE_OPTIONS, VOICE_MODE_OPTIONS, VOICE_OPTIONS } from './persona-voices';
import {
  EMPTY_PERSONA, personaFromForm, personaSchema, personaToForm, personaToInput, type PersonaFormValues,
} from './persona-schema';

const PERSONA_AUTO = 'Авто — цепочка chat:sales';

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
  const [model, name, avatarUrl] = useWatch({ control, name: ['model', 'name', 'avatar_url'] });

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
        <Field label="Модель" hint={modelsUnavailable ? 'Список моделей недоступен — используется авто-цепочка' : undefined}>
          <SelectControl control={control} name="model" options={modelOptions(models, model, PERSONA_AUTO)} />
        </Field>
        <Field label="Стратегия">
          <SelectControl control={control} name="strategy_id" options={strategyOptions} />
        </Field>
      </FormGrid>

      <Field label="Ссылка на аватар" error={errors.avatar_url?.message}>
        <Input {...register('avatar_url')} placeholder="https://…" inputMode="url" />
      </Field>

      <Field
        label="Промпт персонажа"
        hint="Весь промпт персонажа одним текстом: кто она, легенда, роль, стиль речи, что можно и нельзя. Модель получает его как есть."
        error={errors.description?.message}
      >
        <Textarea
          {...register('description')}
          className="min-h-96 max-h-[75vh] font-mono text-[13px] md:text-[13px]"
          placeholder="Ты Марина, 45 лет, из Днепра… Как живёшь в роли, как пишешь, чего не делаешь."
        />
      </Field>
      {persona && <PersonaFullPrompt personaId={persona.id} />}

      <FormGrid className="lg:grid-cols-2">
        <Field label="Голосовые ответы" hint="Голос синтезирует Google Gemini TTS через AIbroker; текст ответа остаётся в диалоге">
          <SelectControl control={control} name="voice_mode" options={VOICE_MODE_OPTIONS} />
        </Field>
        <Field label="Голос (Google Gemini)" error={errors.voice_id?.message}>
          <SelectControl control={control} name="voice_id" options={VOICE_OPTIONS} />
        </Field>
      </FormGrid>

      <FormGrid className="lg:grid-cols-2">
        <Field label="Манера речи (необязательно)" hint="Подсказка для синтеза: как произносить, а не что" error={errors.voice_style?.message}>
          <Input {...register('voice_style')} placeholder="тепло, спокойно, с улыбкой" autoComplete="off" />
        </Field>
        <Field label="Шум записи" hint="Как будто голосовое записано на смартфон в этом месте; голос и произношение те же">
          <SelectControl control={control} name="voice_ambience" options={VOICE_AMBIENCE_OPTIONS} />
        </Field>
      </FormGrid>

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
