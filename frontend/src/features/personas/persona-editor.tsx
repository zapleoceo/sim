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
import { personaFieldAnchor } from './persona-prompt-logic';
import { Section } from './persona-sections';
import { SelectControl } from './select-control';
import { StrategyScenario } from './strategy-scenario';
import { VOICE_AMBIENCE_OPTIONS, VOICE_MODE_OPTIONS, VOICE_OPTIONS } from './persona-voices';
import {
  ageFromBirthDate, EMPTY_PERSONA, GENDERS, personaFromForm, personaSchema, personaToForm, personaToInput,
  type PersonaFormValues,
} from './persona-schema';

const PERSONA_AUTO = 'Авто — цепочка chat:sales';
const MONO = 'font-mono text-[13px] md:text-[13px]';

function birthDateHint(value: string): string {
  const age = ageFromBirthDate(value);
  return `${age === null ? 'Возраст' : `Сейчас ей ${age}. Возраст`}, знак зодиака и дни до дня рождения считает код и подсказывает модели`;
}

/** Everything that is the persona's own: one form, one «Сохранить», sections in page order. */
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
  const [model, name, avatarUrl, birthDate, strategyId] = useWatch({
    control, name: ['model', 'name', 'avatar_url', 'birth_date', 'strategy_id'],
  });

  const strategyOptions: Option<number | null>[] = [
    { value: null, label: 'Не выбрана' },
    ...strategies.map((s) => ({ value: s.id, label: s.name })),
  ];
  const strategy = strategies.find((s) => s.id === strategyId);

  return (
    <form
      noValidate
      onSubmit={handleSubmit((v) => onSave(personaFromForm(v)))}
      className="flex flex-col gap-8 rounded-xl border bg-card p-3 shadow-xs sm:p-5"
    >
      <Section id="profile" hint="Кто она: данные карточки. Возраст и местное время модель получает от кода, а не из текста.">
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
          <Field label="Имя" error={errors.name?.message}>
            <Input {...register('name')} autoComplete="off" />
          </Field>
          <Field label="Пол" hint="Идёт в описание кадра для её фото и кружочков; в промпт переписки не попадает">
            <SelectControl control={control} name="gender" options={GENDERS} />
          </Field>
          <Field
            label="Язык"
            hint="Язык голоса на звонке (ru, uk или en; другое значение: модель определяет сама) и язык подписей к постам. Язык переписки задаёт промпт"
            error={errors.language?.message}
          >
            <Input {...register('language')} autoComplete="off" />
          </Field>
          <Field anchorId={personaFieldAnchor('birth_date')} label="Дата рождения" hint={birthDateHint(birthDate)} error={errors.birth_date?.message}>
            <Input type="date" {...register('birth_date')} />
          </Field>
          <Field anchorId={personaFieldAnchor('timezone')} label="Часовой пояс" hint="Местное время персонажа, например Europe/Kyiv" error={errors.timezone?.message}>
            <Input {...register('timezone')} autoComplete="off" />
          </Field>
          <Field label="Ссылка на аватар" error={errors.avatar_url?.message}>
            <Input {...register('avatar_url')} placeholder="https://…" inputMode="url" />
          </Field>
        </FormGrid>
      </Section>

      <Section id="prompt" hint="Её собственный текст: модель получает его первым и как есть.">
        <Field
          anchorId={personaFieldAnchor('description')}
          label="Промпт персонажа"
          hint="Кто она, легенда, роль, стиль речи, что можно и нельзя."
          error={errors.description?.message}
        >
          <Textarea
            {...register('description')}
            className={`min-h-96 max-h-[75vh] ${MONO}`}
            placeholder="Ты Марина, 45 лет, из Днепра… Как живёшь в роли, как пишешь, чего не делаешь."
          />
        </Field>
        <Field
          anchorId={personaFieldAnchor('prohibitions')}
          label="Запреты"
          hint="Правило на строку. Идут в промпт сразу после её текста, разделом «Чего нельзя никогда». Пусто: раздела нет."
          error={errors.prohibitions?.message}
        >
          <Textarea {...register('prohibitions')} className={`min-h-24 max-h-[50vh] ${MONO}`} placeholder={'не обещать доход\nне обсуждать политику'} />
        </Field>
      </Section>

      <Section id="routine" hint="Где она сейчас, что было вчера и будет завтра, считает код, поэтому ответы всегда совпадают.">
        <Field
          anchorId={personaFieldAnchor('routine')}
          label="Распорядок недели"
          hint="Строка на дело: дни, время, что делает. Дни: пн…вс, пн-пт, сб,вс, ежедневно или дата 2026-11-20 (разовое дело)."
          error={errors.routine?.message}
        >
          <Textarea
            {...register('routine')}
            className={`min-h-48 ${MONO}`}
            placeholder={'пн-пт 06:30-07:15 пробежка вдоль Эльбы\nпн-пт 09:00-13:00 консультации с клиентами, из дома\nвс 13:00-16:00 обед у мамы\nежедневно 23:00-06:30 сплю'}
          />
        </Field>
      </Section>

      <Section id="strategy" hint="Какая модель пишет ответы и по какому сценарию она ведёт разговор.">
        <FormGrid>
          <Field label="Модель" hint={modelsUnavailable ? 'Список моделей недоступен — используется авто-цепочка' : undefined}>
            <SelectControl control={control} name="model" options={modelOptions(models, model, PERSONA_AUTO)} />
          </Field>
          <Field label="Стратегия" hint="Не выбрана: действует первая стратегия из списка">
            <SelectControl control={control} name="strategy_id" options={strategyOptions} />
          </Field>
        </FormGrid>
        {strategy && <StrategyScenario key={strategy.id} strategy={strategy} />}
      </Section>

      <Section id="voice" hint="Голосовые сообщения и живые звонки в Telegram. Правило звонков показано в «Итоговом промпте».">
        <FormGrid>
          <Field label="Голосовые ответы" hint="Голос синтезирует Google Gemini TTS через AIbroker; текст ответа остаётся в диалоге">
            <SelectControl control={control} name="voice_mode" options={VOICE_MODE_OPTIONS} />
          </Field>
          <Field label="Голос (Google Gemini)" error={errors.voice_id?.message}>
            <SelectControl control={control} name="voice_id" options={VOICE_OPTIONS} />
          </Field>
          <Field label="Манера речи (необязательно)" hint="Подсказка для синтеза: как произносить, а не что" error={errors.voice_style?.message}>
            <Input {...register('voice_style')} placeholder="тепло, спокойно, с улыбкой" autoComplete="off" />
          </Field>
          <Field label="Шум записи" hint="Как будто голосовое записано на смартфон в этом месте; голос и произношение те же">
            <SelectControl control={control} name="voice_ambience" options={VOICE_AMBIENCE_OPTIONS} />
          </Field>
        </FormGrid>
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Controller
              control={control}
              name="accept_calls"
              render={({ field }) => (
                <Switch id="persona-accept-calls" checked={field.value} onCheckedChange={field.onChange} />
              )}
            />
            <Label htmlFor="persona-accept-calls">Принимать звонки</Label>
          </div>
          <p className="text-xs text-muted-foreground">
            Отвечает на входящие звонки в Telegram живым голосом (тот же голос, Google Gemini Live через AIbroker), только
            клиентам, с которыми уже есть переписка. Расшифровка звонка попадает в диалог.
          </p>
        </div>
      </Section>

      <div className="sticky bottom-(--bottom-nav) -mx-3 -mb-3 flex items-center gap-2 rounded-b-xl border-t bg-card/95 px-3 py-2 backdrop-blur sm:-mx-5 sm:-mb-5 sm:px-5 sm:py-3">
        {onRemove && (
          <Button variant="ghost-destructive" onClick={onRemove}>
            Удалить
          </Button>
        )}
        <span className="flex-1" />
        <Button type="submit" disabled={saving}>
          {saving ? 'Сохраняем…' : 'Сохранить персонажа'}
        </Button>
      </div>
    </form>
  );
}
