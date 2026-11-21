import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCcw, Save, Sparkles } from 'lucide-react';
import { useMemo, type ReactNode } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';
import { Field, Section, SwitchRow } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';
import { qk } from '@/lib/query';
import type { Persona, Voice } from '@/lib/types';
import { personaApi } from './api';
import { lifeApi } from './life/life-api';
import {
  GENDERS, LANGUAGES, PROMPT_SOFT_LIMIT, REPLY_DELAY_MAX, personaSchema, timeZones, toFormValues, toPatch, withCurrent,
  type PersonaFormValues,
} from './persona-form-logic';

export const PERSONA_FORM_ID = 'persona-form';

const SWITCHES: { name: 'ai_enabled' | 'voice_enabled' | 'photo_enabled' | 'video_enabled' | 'calls_enabled'; label: string; hint: string }[] = [
  { name: 'ai_enabled', label: 'ИИ отвечает', hint: 'Главный выключатель: без него персонаж молчит во всех чатах.' },
  { name: 'voice_enabled', label: 'Голосовые', hint: 'Может отвечать голосовыми.' },
  { name: 'photo_enabled', label: 'Фото', hint: 'Может отправлять фото из медиатеки и генерировать новые.' },
  { name: 'video_enabled', label: 'Видео', hint: 'Может генерировать и отправлять видео.' },
  { name: 'calls_enabled', label: 'Звонки', hint: 'Принимает голосовые звонки.' },
];

export interface PersonaEditorProps {
  persona: Persona;
  voices: readonly Voice[];
  /** Sections with their own Save (agents, life, channel, media library), between the form and its save bar. */
  children?: ReactNode;
}

/**
 * The persona's own settings on one form with one Save button (PUT /personas/{id}, changed fields).
 * Her agents and the guard's rules are edited in «Агенты» (agents/agents-section.tsx).
 */
export function PersonaEditor({ persona, voices, children }: PersonaEditorProps) {
  const queryClient = useQueryClient();
  const initial = useMemo(() => toFormValues(persona), [persona]);
  const form = useForm<PersonaFormValues>({ resolver: zodResolver(personaSchema), defaultValues: initial, mode: 'onBlur' });
  const { register, control, formState, handleSubmit, reset } = form;
  const errors = formState.errors;
  const dirty = formState.isDirty;
  useUnsavedGuard(dirty);

  const save = useMutation({
    // Diff against the form's own baseline: it moves to the saved values after every save.
    mutationFn: (values: PersonaFormValues) =>
      personaApi.update(persona.id, toPatch(values, (formState.defaultValues ?? initial) as PersonaFormValues)),
    onSuccess: (saved) => {
      queryClient.setQueryData(qk.persona(persona.id), saved);
      void queryClient.invalidateQueries({ queryKey: qk.personaList });
      reset(toFormValues(saved));
      toast.success('Настройки персонажа сохранены');
    },
  });

  const describe = useMutation({
    mutationFn: () => lifeApi.describeAppearance(persona.id),
    onSuccess: ({ text, references }) => {
      form.setValue('appearance', text, { shouldDirty: true });
      toast.success(`Описала по ${references} фото — проверьте и сохраните`);
    },
  });

  const prompt = useWatch({ control, name: 'prompt' }) ?? '';
  const gender = useWatch({ control, name: 'gender' });
  const language = useWatch({ control, name: 'language' });
  const voiceId = useWatch({ control, name: 'voice_id' });
  const zones = useMemo(timeZones, []);
  const voiceOptions = withCurrent(voices.map((v) => ({ value: v.id, label: v.label || v.id })), voiceId);

  return (
    <>
      <form
        id={PERSONA_FORM_ID}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={handleSubmit((values) => save.mutate(values))}
      >
        <Section id="basics" title="Основное">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="persona-name" label="Имя" error={errors.name?.message}>
              <Input id="persona-name" autoComplete="off" aria-invalid={!!errors.name} {...register('name')} />
            </Field>
            <Field id="persona-gender" label="Пол">
              <NativeSelect id="persona-gender" {...register('gender')}>
                {withCurrent(GENDERS, gender).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </NativeSelect>
            </Field>
            <Field id="persona-language" label="Язык" error={errors.language?.message}>
              <NativeSelect id="persona-language" {...register('language')}>
                {withCurrent(LANGUAGES, language).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </NativeSelect>
            </Field>
            <Field id="persona-timezone" label="Часовой пояс" hint="По нему код считает её время и время суток." error={errors.timezone?.message}>
              <Input id="persona-timezone" list="persona-timezones" autoComplete="off" aria-invalid={!!errors.timezone} {...register('timezone')} />
              <datalist id="persona-timezones">
                {zones.map((z) => <option key={z} value={z} />)}
              </datalist>
            </Field>
            <Field id="persona-birth" label="Дата рождения" hint="Возраст считает код." error={errors.birth_date?.message}>
              <Input id="persona-birth" type="date" aria-invalid={!!errors.birth_date} {...register('birth_date')} />
            </Field>
          </div>
        </Section>

        <Section id="prompt" title="Промт" description="Кто она, характер, стиль, запреты. Один текст на всё.">
          <Field
            id="persona-prompt"
            label={<span className="sr-only">Промт</span>}
            hint={
              <span className={prompt.length > PROMPT_SOFT_LIMIT ? 'text-warning' : undefined}>
                {prompt.length.toLocaleString('ru-RU')} символов
              </span>
            }
          >
            <Textarea id="persona-prompt" className="max-h-[70vh] min-h-72 font-mono text-[13px]" {...register('prompt')} />
          </Field>
        </Section>

        <Section
          id="appearance"
          title="Внешность"
          description="Точное описание по-английски для генерации фото и видео: глаза, волосы, лицо, фигура. Уходит в каждый кадр."
          actions={
            <Button variant="outline" size="sm" disabled={describe.isPending} onClick={() => describe.mutate()}>
              {describe.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
              Описать по референсам
            </Button>
          }
        >
          <Field id="persona-appearance" label={<span className="sr-only">Внешность</span>} hint="После описания проверьте текст и сохраните форму.">
            <Textarea id="persona-appearance" className="min-h-24" {...register('appearance')} />
          </Field>
        </Section>

        <Section id="voice" title="Голос" description="Для голосовых ответов (модель и ключи — в «Агентах», агент «Голос»).">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="persona-voice" label="Голос">
              <NativeSelect id="persona-voice" {...register('voice_id')}>
                <option value="">— не выбран —</option>
                {voiceOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </NativeSelect>
            </Field>
            <Field id="persona-voice-style" label="Манера речи" hint="Интонация, темп, настроение — подсказка для TTS.">
              <Textarea id="persona-voice-style" className="min-h-9" rows={2} {...register('voice_style')} />
            </Field>
          </div>
          <SwitchRow
            id="persona-voice_ambience_enabled"
            label="Фон в голосовых"
            hint="Голосовое звучит как записанное на телефон там, где она сейчас: кафе, улица, кухня. Ночью дома — тишина."
          >
            <Controller
              control={control}
              name="voice_ambience_enabled"
              render={({ field }) => (
                <Switch
                  id="persona-voice_ambience_enabled"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  onBlur={field.onBlur}
                  ref={field.ref}
                />
              )}
            />
          </SwitchRow>
        </Section>

        <Section id="switches" title="Переключатели">
          <div className="flex flex-col divide-y">
            {SWITCHES.map((s) => (
              <SwitchRow key={s.name} id={`persona-${s.name}`} label={s.label} hint={s.hint}>
                <Controller
                  control={control}
                  name={s.name}
                  render={({ field }) => (
                    <Switch
                      id={`persona-${s.name}`}
                      checked={field.value}
                      onCheckedChange={field.onChange}
                      onBlur={field.onBlur}
                      ref={field.ref}
                    />
                  )}
                />
              </SwitchRow>
            ))}
            <div className="pt-3">
              <Field
                id="persona-delay"
                label="Пауза перед ответом, секунд"
                hint="Ждёт, вдруг клиент пишет несколькими сообщениями."
                error={errors.reply_delay_s?.message}
                className="max-w-56"
              >
                <Input
                  id="persona-delay"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={REPLY_DELAY_MAX}
                  aria-invalid={!!errors.reply_delay_s}
                  {...register('reply_delay_s')}
                />
              </Field>
            </div>
          </div>
        </Section>
      </form>

      {children}

      <div
        className="sticky bottom-(--bottom-nav) z-30 -mx-3 mt-2 flex flex-wrap items-center justify-end gap-2 border-t bg-background/95 px-3 py-2.5 backdrop-blur sm:-mx-6 sm:px-6"
        role="region"
        aria-label="Сохранение"
      >
        <span className="mr-auto text-sm text-muted-foreground" aria-live="polite">
          {dirty ? 'Есть несохранённые изменения' : 'Все изменения сохранены'}
        </span>
        <Button variant="ghost" onClick={() => reset()} disabled={!dirty || save.isPending}>
          <RotateCcw />
          Отменить
        </Button>
        <Button type="submit" form={PERSONA_FORM_ID} disabled={!dirty || save.isPending}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Сохранить
        </Button>
      </div>
    </>
  );
}
