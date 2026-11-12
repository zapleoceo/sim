import type { Option } from '@/components/option-select';
import type { VoiceMode } from '@/lib/models';

/**
 * Женские голоса Google Gemini TTS (синтез идёт через AIbroker), проверенные на русском и
 * украинском. Список совпадает с backend `sim.domain.voice.GEMINI_VOICES`; подпись — характер
 * голоса по описанию Google.
 */
export const GEMINI_VOICES: readonly { id: string; label: string }[] = [
  { id: 'Kore', label: 'Kore — уверенный' },
  { id: 'Aoede', label: 'Aoede — лёгкий' },
  { id: 'Leda', label: 'Leda — молодой' },
  { id: 'Zephyr', label: 'Zephyr — яркий' },
  { id: 'Callirrhoe', label: 'Callirrhoe — спокойный' },
  { id: 'Autonoe', label: 'Autonoe — яркий' },
  { id: 'Despina', label: 'Despina — мягкий' },
  { id: 'Erinome', label: 'Erinome — чёткий' },
  { id: 'Achernar', label: 'Achernar — мягкий' },
  { id: 'Gacrux', label: 'Gacrux — зрелый' },
  { id: 'Pulcherrima', label: 'Pulcherrima — открытый' },
  { id: 'Vindemiatrix', label: 'Vindemiatrix — нежный' },
  { id: 'Sulafat', label: 'Sulafat — тёплый' },
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
