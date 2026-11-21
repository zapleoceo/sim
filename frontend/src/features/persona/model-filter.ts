import type { BrokerModel, Persona } from '@/lib/types';

export type ModelField =
  | 'model_reply' | 'model_guard' | 'model_vision' | 'model_transcribe' | 'model_memory'
  | 'model_tts' | 'model_image' | 'model_video' | 'model_live' | 'model_life';

export type ModelPurpose =
  | 'reply' | 'guard' | 'memory' | 'vision' | 'transcribe' | 'tts' | 'image' | 'video' | 'live' | 'life';

export interface PurposeInfo {
  purpose: ModelPurpose;
  field: ModelField & keyof Persona;
  label: string;
  hint: string;
}

/** One select per purpose on the persona page, in this order. */
export const MODEL_PURPOSES: readonly PurposeInfo[] = [
  { purpose: 'reply', field: 'model_reply', label: 'Ответы', hint: 'Основная: пишет план ответа (JSON).' },
  { purpose: 'guard', field: 'model_guard', label: 'Проверка', hint: 'Дешёвая и быстрая: смотрит ответ перед отправкой.' },
  { purpose: 'vision', field: 'model_vision', label: 'Распознавание', hint: 'Описывает фото и видео клиента.' },
  { purpose: 'transcribe', field: 'model_transcribe', label: 'Расшифровка', hint: 'Голосовые в текст.' },
  { purpose: 'memory', field: 'model_memory', label: 'Память', hint: 'Сжимает старую переписку, ведёт память о клиенте.' },
  { purpose: 'tts', field: 'model_tts', label: 'Голос (TTS)', hint: 'Озвучивает голосовые ответы.' },
  { purpose: 'image', field: 'model_image', label: 'Фото', hint: 'Генерирует фото по референсам внешности.' },
  { purpose: 'video', field: 'model_video', label: 'Видео', hint: 'Генерирует видео.' },
  { purpose: 'live', field: 'model_live', label: 'Звонки', hint: 'Живой голосовой разговор.' },
  { purpose: 'life', field: 'model_life', label: 'Жизнь (план дня)', hint: 'Раз в день планирует её день: события, одежду, истории.' },
];

const has = (list: readonly string[] | null | undefined, value: string): boolean =>
  (list ?? []).some((v) => v.toLowerCase() === value);

/** Does the Broker model fit this purpose (by its modalities and capabilities)? */
export function matchesPurpose(model: BrokerModel, purpose: ModelPurpose): boolean {
  const input = model.input_modalities;
  const output = model.output_modalities;
  switch (purpose) {
    case 'reply':
    case 'guard':
    case 'memory':
    case 'life':
      return has(input, 'text') && has(output, 'text');
    case 'vision':
      return has(input, 'image') || has(input, 'video');
    case 'transcribe':
      return has(input, 'audio') && has(output, 'text');
    case 'tts':
      return has(output, 'audio');
    case 'image':
      return has(output, 'image');
    case 'video':
      return has(output, 'video');
    case 'live':
      return has(model.capabilities, 'live') || (has(input, 'audio') && has(output, 'audio'));
  }
}

export interface ModelOption {
  value: string;
  label: string;
  available: boolean | null;
  /** The saved value that is not in the filtered list (kept so saving never drops it). */
  outsideFilter: boolean;
}

export function availabilityLabel(available: boolean | null): string {
  if (available === false) return 'недоступна';
  if (available === null) return 'доступность неизвестна';
  return 'доступна';
}

/**
 * Options of one select: models that fit the purpose (available first, then by id), plus the
 * current value when it does not fit or is gone from the catalog.
 */
export function modelOptions(models: readonly BrokerModel[], purpose: ModelPurpose, current: string): ModelOption[] {
  const fit = models
    .filter((m) => matchesPurpose(m, purpose))
    .sort((a, b) => Number(b.available === true) - Number(a.available === true) || a.id.localeCompare(b.id));
  const options: ModelOption[] = fit.map((m) => ({
    value: m.id,
    label: m.available === true ? m.id : `${m.id} — ${availabilityLabel(m.available)}`,
    available: m.available,
    outsideFilter: false,
  }));
  if (current && !fit.some((m) => m.id === current)) {
    const known = models.find((m) => m.id === current);
    options.unshift({
      value: current,
      label: known ? `${current} — не подходит по возможностям` : `${current} — нет в каталоге Broker`,
      available: known ? known.available : null,
      outsideFilter: true,
    });
  }
  return options;
}
