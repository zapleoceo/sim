import type { Option } from '@/components/option-select';
import type { VoiceMode } from '@/lib/models';

/**
 * Голоса Google Gemini TTS (синтез идёт через AIbroker). Список совпадает с
 * backend `sim.domain.voice.GEMINI_VOICES`; подпись — характер голоса по описанию Google.
 */
export const GEMINI_VOICES: readonly { id: string; label: string }[] = [
  { id: 'Kore', label: 'Kore — уверенный, женский' },
  { id: 'Aoede', label: 'Aoede — лёгкий, женский' },
  { id: 'Leda', label: 'Leda — молодой, женский' },
  { id: 'Zephyr', label: 'Zephyr — яркий, женский' },
  { id: 'Callirrhoe', label: 'Callirrhoe — спокойный, женский' },
  { id: 'Autonoe', label: 'Autonoe — яркий, женский' },
  { id: 'Despina', label: 'Despina — мягкий, женский' },
  { id: 'Erinome', label: 'Erinome — чёткий, женский' },
  { id: 'Laomedeia', label: 'Laomedeia — бодрый, женский' },
  { id: 'Achernar', label: 'Achernar — мягкий, женский' },
  { id: 'Gacrux', label: 'Gacrux — зрелый, женский' },
  { id: 'Pulcherrima', label: 'Pulcherrima — открытый, женский' },
  { id: 'Vindemiatrix', label: 'Vindemiatrix — нежный, женский' },
  { id: 'Sulafat', label: 'Sulafat — тёплый, женский' },
  { id: 'Puck', label: 'Puck — бодрый, мужской' },
  { id: 'Charon', label: 'Charon — информативный, мужской' },
  { id: 'Fenrir', label: 'Fenrir — живой, мужской' },
  { id: 'Orus', label: 'Orus — твёрдый, мужской' },
  { id: 'Enceladus', label: 'Enceladus — с придыханием, мужской' },
  { id: 'Iapetus', label: 'Iapetus — ясный, мужской' },
  { id: 'Umbriel', label: 'Umbriel — спокойный, мужской' },
  { id: 'Algieba', label: 'Algieba — ровный, мужской' },
  { id: 'Algenib', label: 'Algenib — с хрипотцой, мужской' },
  { id: 'Rasalgethi', label: 'Rasalgethi — информативный, мужской' },
  { id: 'Alnilam', label: 'Alnilam — твёрдый, мужской' },
  { id: 'Schedar', label: 'Schedar — ровный, мужской' },
  { id: 'Achird', label: 'Achird — дружелюбный, мужской' },
  { id: 'Zubenelgenubi', label: 'Zubenelgenubi — непринуждённый, мужской' },
  { id: 'Sadachbia', label: 'Sadachbia — оживлённый, мужской' },
  { id: 'Sadaltager', label: 'Sadaltager — знающий, мужской' },
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
