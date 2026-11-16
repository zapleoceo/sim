import { zodResolver } from '@hookform/resolvers/zod';
import { Controller, useForm, useWatch, type Control, type FieldErrors } from 'react-hook-form';
import { Avatar } from '@/components/avatar';
import { Field, FormGrid } from '@/components/field';
import { LongText } from '@/components/long-text';
import { modelOptions } from '@/components/model-options';
import type { Option } from '@/components/option-select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { BrokerModel, Persona, PersonaIn, Strategy } from '@/lib/models';
import { BLOCKS, Section, useSections, type SectionId } from './persona-sections';
import { SelectControl } from './select-control';
import { StrategyScenario } from './strategy-scenario';
import { VOICE_AMBIENCE_OPTIONS, VOICE_MODE_OPTIONS, VOICE_OPTIONS } from './persona-voices';
import { promptDuplicates } from './prompt-duplicates';
import {
  ageFromBirthDate, EMPTY_PERSONA, GENDERS, personaFromForm, personaSchema, personaToForm, personaToInput,
  type PersonaFormValues,
} from './persona-schema';

const PERSONA_AUTO = 'Авто — цепочка chat:sales';
/** A switch with its label: the whole row is one touch target. */
const TOGGLE_ROW = 'flex min-h-[40px] items-center gap-2.5';

/** The block every form field lives in: a field that failed validation opens its block. */
const FIELD_SECTION: Record<keyof PersonaFormValues, SectionId> = {
  name: 'profile', is_active: 'profile', gender: 'profile', birth_date: 'profile',
  timezone: 'profile', avatar_url: 'profile',
  agent_prompt: 'prompt',
  routine: 'routine', language: 'voice',
  voice_mode: 'voice', voice_id: 'voice', voice_style: 'voice', voice_ambience: 'voice', accept_calls: 'voice',
  model: 'strategy', strategy_id: 'strategy',
};

function sectionsWithErrors(errors: FieldErrors<PersonaFormValues>): SectionId[] {
  const ids = Object.keys(errors).map((field) => FIELD_SECTION[field as keyof PersonaFormValues]);
  return [...new Set(ids.filter((id): id is SectionId => id !== undefined))];
}

function birthDateHint(value: string): string {
  const age = ageFromBirthDate(value);
  return `${age === null ? 'Возраст' : `Сейчас ей ${age}. Возраст`}, знак зодиака и дни до дня рождения считает код и подсказывает модели`;
}

const PROMPT_HINT =
  'Кто она, легенда, характер, стиль, язык переписки, запреты — всё здесь. Не пишите сюда дату рождения, возраст, '
  + 'часовой пояс и распорядок: они в полях, код сам считает возраст, время и где она сейчас и добавляет это модели.';

/** Non-blocking: names the fields the prompt repeats, so each fact stays in one place. */
function PromptDuplicates({ control }: { control: Control<PersonaFormValues> }) {
  const text = useWatch({ control, name: 'agent_prompt' });
  const labels = promptDuplicates(text ?? '');
  if (labels.length === 0) return null;
  return (
    <p role="note" className="text-xs text-warning">
      В промпте найдено то, что уже есть в полях: {labels.join(', ')}. Оставьте это в одном месте — в полях.
    </p>
  );
}

const sameBody = (a: PersonaIn, b: PersonaIn): boolean => JSON.stringify(a) === JSON.stringify(b);

/**
 * «Сохранить» of the persona form, stuck to the bottom of the screen while the form is in view (above the
 * phone tab bar; in the flow while the keyboard is open, see index.css). Says when the form differs from
 * what is saved: a text edited in a dialog is not saved until this button is pressed.
 */
function SaveBar({ control, saved, saving, onRemove }: {
  control: Control<PersonaFormValues>;
  saved: PersonaIn;
  saving: boolean;
  onRemove?: () => void;
}) {
  const values = useWatch({ control }) as PersonaFormValues;
  const dirty = !sameBody(personaFromForm(values), personaFromForm(personaToForm(saved)));
  return (
    <div
      data-slot="save-bar"
      className="sticky bottom-(--bottom-nav) z-10 flex items-center gap-2 rounded-b-xl border-t bg-card/95 px-3 py-2 backdrop-blur sm:px-5"
    >
      {onRemove && (
        <Button variant="ghost-destructive" onClick={onRemove}>
          Удалить
        </Button>
      )}
      <span role="status" className="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground">
        {dirty ? 'Есть несохранённые правки' : ''}
      </span>
      <Button type="submit" disabled={saving}>
        {saving ? 'Сохраняем…' : 'Сохранить персонажа'}
      </Button>
    </div>
  );
}

/**
 * Everything that is the persona's own: one form, one «Сохранить», blocks in page order. A collapsed
 * block keeps its fields in the form; long texts are edited in a dialog and saved with the rest.
 */
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
  const saved = persona ? personaToInput(persona) : EMPTY_PERSONA;
  const form = useForm<PersonaFormValues>({
    resolver: zodResolver(personaSchema),
    defaultValues: personaToForm(saved),
    mode: 'onTouched',
  });
  const { register, control, handleSubmit, formState } = form;
  const errors = formState.errors;
  const { reveal } = useSections();
  const invalid = sectionsWithErrors(errors);
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
      onSubmit={handleSubmit((v) => onSave(personaFromForm(v)), (bad) => reveal(sectionsWithErrors(bad)))}
      className={BLOCKS}
    >
      <Section
        id="profile"
        invalid={invalid.includes('profile')}
      >
        <div className="flex items-center gap-3">
          <Avatar name={name || '?'} url={avatarUrl} size={44} />
          <div className={TOGGLE_ROW}>
            <Controller
              control={control}
              name="is_active"
              render={({ field }) => (
                <Switch id="persona-active" checked={field.value} onCheckedChange={field.onChange} />
              )}
            />
            <Label htmlFor="persona-active" className="py-2">Активен</Label>
          </div>
        </div>
        <FormGrid className="lg:grid-cols-3">
          <Field label="Имя" error={errors.name?.message}>
            <Input {...register('name')} autoComplete="off" />
          </Field>
          <Field label="Пол" hint="Идёт в описание кадра для её фото и кружочков; в промпт переписки не попадает">
            <SelectControl control={control} name="gender" options={GENDERS} />
          </Field>
          <Field label="Дата рождения" hint={birthDateHint(birthDate)} error={errors.birth_date?.message}>
            <Input type="date" {...register('birth_date')} />
          </Field>
          <Field label="Часовой пояс" hint="Местное время персонажа, например Europe/Kyiv" error={errors.timezone?.message}>
            <Input {...register('timezone')} autoComplete="off" />
          </Field>
          <Field label="Ссылка на аватар" error={errors.avatar_url?.message}>
            <Input {...register('avatar_url')} placeholder="https://…" inputMode="url" />
          </Field>
        </FormGrid>
      </Section>

      <Section id="prompt" invalid={invalid.includes('prompt')}>
        <Field label="Промпт" hint={PROMPT_HINT} error={errors.agent_prompt?.message}>
          <Textarea
            {...register('agent_prompt')}
            className="min-h-[28rem] max-h-[75vh] font-mono text-[13px] md:text-[13px]"
          />
        </Field>
        <PromptDuplicates control={control} />
      </Section>

      <Section
        id="routine"
        invalid={invalid.includes('routine')}
        hint="Где она сейчас, что было вчера и будет завтра, считает код из этих строк."
      >
        <Controller
          control={control}
          name="routine"
          render={({ field }) => (
            <LongText
              label="Распорядок недели"
              hint="Строка на дело: дни, время, что делает. Дни: пн…вс, пн-пт, сб,вс, ежедневно или дата 2026-11-20 (разовое дело)."
              error={errors.routine?.message}
              value={field.value}
              onChange={field.onChange}
              onBlur={field.onBlur}
              placeholder={'пн-пт 06:30-07:15 пробежка вдоль Эльбы\nпн-пт 09:00-13:00 консультации с клиентами, из дома\nвс 13:00-16:00 обед у мамы\nежедневно 23:00-06:30 сплю'}
            />
          )}
        />
      </Section>

      <Section
        id="voice"
        invalid={invalid.includes('voice')}
        hint="Голосовые сообщения и живые звонки в Telegram."
      >
        <FormGrid>
          <Field
            label="Язык"
            hint="Язык голоса на звонке (ru, uk или en) и подписей к постам. Язык переписки задаёт промпт."
            error={errors.language?.message}
          >
            <Input {...register('language')} autoComplete="off" />
          </Field>
          <Field label="Голосовые ответы" hint="Голос синтезирует Google Gemini TTS через AIbroker; текст ответа остаётся в диалоге">
            <SelectControl control={control} name="voice_mode" options={VOICE_MODE_OPTIONS} />
          </Field>
          <Field label="Голос (Google Gemini)" error={errors.voice_id?.message}>
            <SelectControl control={control} name="voice_id" options={VOICE_OPTIONS} />
          </Field>
          <Field label="Манера речи (необязательно)" hint="Подсказка для синтеза: как произносить, а не что" error={errors.voice_style?.message}>
            <Input {...register('voice_style')} placeholder="тепло, спокойно, с улыбкой" autoComplete="off" />
          </Field>
          <Field label="Шум записи" hint="По обстановке: явное место из текущего распорядка для звонков и голосовых; если место неизвестно, чистый звук. Остальные режимы только для голосовых.">
            <SelectControl control={control} name="voice_ambience" options={VOICE_AMBIENCE_OPTIONS} />
          </Field>
        </FormGrid>
        <div className="flex flex-col gap-1">
          <div className={TOGGLE_ROW}>
            <Controller
              control={control}
              name="accept_calls"
              render={({ field }) => (
                <Switch id="persona-accept-calls" checked={field.value} onCheckedChange={field.onChange} />
              )}
            />
            <Label htmlFor="persona-accept-calls" className="py-2">Принимать звонки</Label>
          </div>
          <p className="text-xs text-muted-foreground">
            Отвечает на входящие звонки в Telegram живым голосом (тот же голос, Google Gemini Live через AIbroker), только
            клиентам, с которыми уже есть переписка. Расшифровка звонка попадает в диалог.
          </p>
        </div>
      </Section>

      <Section id="strategy" invalid={invalid.includes('strategy')} hint="Какая модель пишет ответы и по какому сценарию она ведёт разговор.">
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

      <SaveBar control={control} saved={saved} saving={saving} onRemove={onRemove} />
    </form>
  );
}
