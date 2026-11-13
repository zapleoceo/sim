import type { Option } from '@/components/option-select';
import type { VoiceAmbience, VoiceMode } from '@/lib/models';

/**
 * Голоса Google Gemini TTS (синтез идёт через AIbroker): Kore и Vindemiatrix, их владелец проверил и выбрал
 * (остальные проверенные женские голоса убраны). Список совпадает с backend `sim.domain.voice.GEMINI_VOICES`; подпись — характер
 * голоса по описанию Google.
 */
export const GEMINI_VOICES: readonly { id: string; label: string }[] = [
  { id: 'Kore', label: 'Kore — уверенный' },
  { id: 'Vindemiatrix', label: 'Vindemiatrix — мягкий, домашний' },
];

export const VOICE_MODES: readonly { id: VoiceMode; label: string }[] = [
  { id: 'off', label: 'Только текст' },
  { id: 'mirror', label: 'Голосом на голосовое клиента' },
  { id: 'always', label: 'Всегда голосом' },
];

/** Варианты для выпадающих списков редактора персонажа. */
export const VOICE_OPTIONS: Option<string>[] = [
  { value: '', label: 'Без голоса' },
  ...GEMINI_VOICES.map((v) => ({ value: v.id, label: v.label })),
];
export const VOICE_MODE_OPTIONS: Option<VoiceMode>[] = VOICE_MODES.map((m) => ({ value: m.id, label: m.label }));

/** «Запись на смартфон»: фон и микрофон телефона; произношение не меняется (backend `sim.adapters.ambience`). */
export const VOICE_AMBIENCE_OPTIONS: Option<VoiceAmbience>[] = [
  { value: 'off', label: 'Без шума — чистый звук' },
  { value: 'room', label: 'Дома — комната' },
  { value: 'street', label: 'На улице' },
  { value: 'cafe', label: 'В кафе' },
  { value: 'random', label: 'Случайно: дом, улица или кафе' },
];
