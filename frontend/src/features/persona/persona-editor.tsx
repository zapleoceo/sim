import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCcw, Save, Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Field, Section, SwitchRow } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useDraft } from '@/hooks/use-draft';
import { qk } from '@/lib/query';
import type { Persona, Voice } from '@/lib/types';
import { personaApi } from './api';
import { CallRecordingsPurge } from './call-recordings-purge';
import { lifeApi } from './life/life-api';
import {
  GENDERS, LANGUAGES, PROMPT_SOFT_LIMIT, RECORDING_DAYS, REPLY_DELAY_MAX, personaFormErrors, timeZones, toFormValues, toPatch, withCurrent,
  type PersonaFormValues,
} from './persona-form-logic';
import { VoicePicker } from './voice-picker';
import { VOICE_SPEED, formatSpeed, normalizeSpeed } from './voice-picker-logic';

export const PERSONA_FORM_ID = 'persona-form';
/** The form's name in «Не сохранено: …» and in its save bar. */
export const PERSONA_FORM_NAME = 'Настройки персонажа';

type SwitchName = 'ai_enabled' | 'voice_enabled' | 'photo_enabled' | 'video_enabled' | 'calls_enabled';

const SWITCHES: { name: SwitchName; label: string; hint: string }[] = [
  { name: 'ai_enabled', label: 'ИИ отвечает', hint: 'Главный выключатель: без него персонаж молчит во всех чатах.' },
  { name: 'voice_enabled', label: 'Голосовые', hint: 'Может отвечать голосовыми.' },
  { name: 'photo_enabled', label: 'Фото', hint: 'Может отправлять фото из медиатеки и генерировать новые.' },
  { name: 'video_enabled', label: 'Видео', hint: 'Может генерировать и отправлять видео.' },
  { name: 'calls_enabled', label: 'Звонки', hint: 'Принимает голосовые звонки.' },
];

export interface PersonaEditorProps {
  persona: Persona;
  voices: readonly Voice[];
}

/**
 * The persona's own settings on one form with one Save button (PUT /personas/{id}, changed fields).
 * Her agents, life, channel and media are separate sections of the page with their own Save
 * (persona-page.tsx lays them out); the save bar here belongs to this form only.
 */
export function PersonaEditor({ persona, voices }: PersonaEditorProps) {
  const queryClient = useQueryClient();
  const server = useMemo(() => toFormValues(persona), [persona]);
  const { draft: values, setDraft, baseline, dirty, reset, commit } = useDraft<PersonaFormValues>(server, { guardName: PERSONA_FORM_NAME });
  // Problems are shown after the first Save and then follow the typing.
  const [checked, setChecked] = useState(false);
  const errors = checked ? personaFormErrors(values) : {};
  const set = <K extends keyof PersonaFormValues>(key: K, value: PersonaFormValues[K]): void =>
    setDraft((v) => ({ ...v, [key]: value }));

  const save = useMutation({
    // The difference against the copy the edit started from (a live refresh never adds fields).
    mutationFn: (next: PersonaFormValues) => personaApi.update(persona.id, toPatch(next, baseline)),
    onSuccess: (saved) => {
      queryClient.setQueryData(qk.persona(persona.id), saved);
      void queryClient.invalidateQueries({ queryKey: qk.personaList });
      commit(toFormValues(saved));
      setChecked(false);
      toast.success('Настройки персонажа сохранены');
    },
  });

  const describe = useMutation({
    mutationFn: () => lifeApi.describeAppearance(persona.id),
    onSuccess: ({ text, references }) => {
      set('appearance', text);
      toast.success(`Описала по ${references} фото — проверьте и сохраните`);
    },
  });

  const zones = useMemo(timeZones, []);
  const text = (
    key: 'name' | 'timezone' | 'birth_date' | 'prompt' | 'appearance' | 'voice_style' | 'reply_delay_s' | 'call_recording_days',
  ) => ({
    value: values[key],
    onChange: (e: { target: { value: string } }) => set(key, e.target.value),
    'aria-invalid': errors[key] ? true : undefined,
  });

  return (
    <div className="flex flex-col gap-4">
      <form
        id={PERSONA_FORM_ID}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          setChecked(true);
          if (Object.keys(personaFormErrors(values)).length === 0) save.mutate(values);
        }}
      >
        <Section id="basics" title="Основное">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="persona-name" label="Имя" error={errors.name}>
              <Input id="persona-name" autoComplete="off" {...text('name')} />
            </Field>
            <Field id="persona-gender" label="Пол">
              <NativeSelect id="persona-gender" value={values.gender} onChange={(e) => set('gender', e.target.value)}>
                {withCurrent(GENDERS, values.gender).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </NativeSelect>
            </Field>
            <Field id="persona-language" label="Язык" error={errors.language}>
              <NativeSelect id="persona-language" value={values.language} onChange={(e) => set('language', e.target.value)}>
                {withCurrent(LANGUAGES, values.language).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </NativeSelect>
            </Field>
            <Field id="persona-timezone" label="Часовой пояс" hint="По нему код считает её время и время суток." error={errors.timezone}>
              <Input id="persona-timezone" list="persona-timezones" autoComplete="off" {...text('timezone')} />
              <datalist id="persona-timezones">
                {zones.map((z) => <option key={z} value={z} />)}
              </datalist>
            </Field>
            <Field id="persona-birth" label="Дата рождения" hint="Возраст считает код." error={errors.birth_date}>
              <Input id="persona-birth" type="date" {...text('birth_date')} />
            </Field>
          </div>
        </Section>

        <Section id="prompt" title="Промт" description="Кто она, характер, стиль, запреты. Один текст на всё.">
          <Field
            id="persona-prompt"
            label={<span className="sr-only">Промт</span>}
            hint={
              <span className={values.prompt.length > PROMPT_SOFT_LIMIT ? 'text-warning' : undefined}>
                {values.prompt.length.toLocaleString('ru-RU')} символов
              </span>
            }
          >
            <Textarea id="persona-prompt" className="max-h-[70vh] min-h-72 font-mono text-[13px]" {...text('prompt')} />
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
            <Textarea id="persona-appearance" className="min-h-24" {...text('appearance')} />
          </Field>
        </Section>

        <Section id="voice" title="Голос" description="Для голосовых ответов и звонков (модель и ключи — в «Агентах», агент «Голос»).">
          <Field id="persona-voice" label="Голос" hint="▶ — послушать образец. Первый раз озвучка занимает несколько секунд.">
            <VoicePicker
              id="persona-voice"
              value={values.voice_id}
              onChange={(v) => set('voice_id', v)}
              voices={voices}
              personaId={persona.id}
              speed={values.voice_speed}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              id="persona-voice-speed"
              label="Скорость речи"
              hint="Голосовые ускоряются без изменения тона; в звонке — тоже. 1.0 — как у модели."
              error={errors.voice_speed}
            >
              <div className="flex h-9 items-center gap-3">
                <input
                  id="persona-voice-speed"
                  type="range"
                  min={VOICE_SPEED.min}
                  max={VOICE_SPEED.max}
                  step={VOICE_SPEED.step}
                  value={values.voice_speed}
                  onChange={(e) => set('voice_speed', normalizeSpeed(Number(e.target.value)))}
                  aria-valuetext={formatSpeed(values.voice_speed)}
                  className="min-w-0 flex-1 accent-primary"
                />
                <output htmlFor="persona-voice-speed" className="w-12 text-right text-sm font-medium tabular">
                  {formatSpeed(values.voice_speed)}
                </output>
              </div>
            </Field>
            <Field id="persona-voice-style" label="Манера речи" hint="Интонация, темп, настроение — подсказка для TTS.">
              <Textarea id="persona-voice-style" className="min-h-9" rows={2} {...text('voice_style')} />
            </Field>
          </div>
          <SwitchRow
            id="persona-voice_ambience_enabled"
            label="Фон в голосовых"
            hint="Голосовое звучит как записанное на телефон там, где она сейчас: кафе, улица, кухня. Ночью дома — тишина."
          >
            <Switch
              id="persona-voice_ambience_enabled"
              checked={values.voice_ambience_enabled}
              onCheckedChange={(v) => set('voice_ambience_enabled', v)}
            />
          </SwitchRow>
          {/* Call recordings: copied with the voice part by «Клонировать». */}
          <div className="flex flex-col gap-3 border-t pt-2">
            <SwitchRow
              id="persona-call_recording"
              label="Записывать звонки"
              hint="Разговор сохраняется в стерео: собеседник слева, Марина справа. Слушать — в ленте чата у звонка."
            >
              <Switch
                id="persona-call_recording"
                checked={values.call_recording}
                onCheckedChange={(v) => set('call_recording', v)}
              />
            </SwitchRow>
            <div className="flex flex-col items-start gap-3">
              <Field
                id="persona-call-recording-days"
                label="Хранить записи, дней"
                hint="Старше — удаляются автоматически раз в час. Расшифровка звонка остаётся."
                error={errors.call_recording_days}
                className="max-w-72"
              >
                <Input
                  id="persona-call-recording-days"
                  type="number"
                  inputMode="numeric"
                  min={RECORDING_DAYS.min}
                  max={RECORDING_DAYS.max}
                  {...text('call_recording_days')}
                />
              </Field>
              {/* The saved period: the server purges by it, not by an unsaved edit. */}
              <CallRecordingsPurge personaId={persona.id} days={persona.call_recording_days ?? RECORDING_DAYS.default} />
            </div>
          </div>
        </Section>

        <Section id="switches" title="Переключатели">
          <div className="flex flex-col divide-y">
            {SWITCHES.map((s) => (
              <SwitchRow key={s.name} id={`persona-${s.name}`} label={s.label} hint={s.hint}>
                <Switch id={`persona-${s.name}`} checked={values[s.name]} onCheckedChange={(v) => set(s.name, v)} />
              </SwitchRow>
            ))}
            <div className="pt-3">
              <Field
                id="persona-delay"
                label="Пауза перед ответом, секунд"
                hint="Ждёт, вдруг клиент пишет несколькими сообщениями."
                error={errors.reply_delay_s}
                className="max-w-56"
              >
                <Input id="persona-delay" type="number" inputMode="numeric" min={0} max={REPLY_DELAY_MAX} {...text('reply_delay_s')} />
              </Field>
            </div>
          </div>
        </Section>
      </form>

      {/* Sticky only inside this container: it never claims to save the sections below. */}
      <div
        className="sticky bottom-(--bottom-nav) z-30 -mx-3 flex flex-wrap items-center justify-end gap-2 border-t bg-background/95 px-3 py-2.5 backdrop-blur sm:-mx-6 sm:px-6"
        role="region"
        aria-label={`Сохранение: ${PERSONA_FORM_NAME.toLowerCase()}`}
      >
        <span className="mr-auto text-sm text-muted-foreground" aria-live="polite">
          {dirty ? 'Настройки персонажа изменены, не сохранены' : 'Настройки персонажа сохранены'}
        </span>
        <Button variant="ghost" onClick={() => { reset(); setChecked(false); }} disabled={!dirty || save.isPending}>
          <RotateCcw />
          Отменить
        </Button>
        <Button type="submit" form={PERSONA_FORM_ID} disabled={!dirty || save.isPending}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Сохранить
        </Button>
      </div>
    </div>
  );
}
