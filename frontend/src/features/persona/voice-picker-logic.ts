import type { SegmentedOption } from '@/components/segmented';
import type { Voice, VoiceGender } from '@/lib/types';

/** «Все / Женские / Мужские» above the voice list. */
export type VoiceFilter = 'all' | VoiceGender;

export const VOICE_FILTERS: readonly SegmentedOption<VoiceFilter>[] = [
  { value: 'all', label: 'Все' },
  { value: 'female', label: 'Женские' },
  { value: 'male', label: 'Мужские' },
];

export const VOICE_GENDER_LABEL: Record<VoiceGender, string> = { female: 'женский', male: 'мужской' };

/** The voices of the chosen gender whose id, label or tone contains the search text (any case). */
export function filterVoices(voices: readonly Voice[], filter: VoiceFilter, query: string): Voice[] {
  const q = query.trim().toLowerCase();
  return voices.filter((v) => {
    if (filter !== 'all' && v.gender !== filter) return false;
    if (!q) return true;
    return [v.id, v.label, v.tone].some((s) => (s ?? '').toLowerCase().includes(q));
  });
}

/** «уверенный, женский»: what the row shows after the voice's name. */
export function voiceTraits(voice: Pick<Voice, 'gender' | 'tone'>): string {
  return [voice.tone?.trim(), VOICE_GENDER_LABEL[voice.gender]].filter(Boolean).join(', ');
}

/**
 * The selected voice in one line: «Kore — уверенный, женский». A saved id that is not in the list
 * (an old voice, or the list did not load) is shown as is; '' = not chosen.
 */
export function selectedVoiceText(voices: readonly Voice[], id: string): string {
  if (!id) return 'не выбран';
  const voice = voices.find((v) => v.id === id);
  if (!voice) return id;
  const traits = voiceTraits(voice);
  const name = voice.label || voice.id;
  return traits ? `${name} — ${traits}` : name;
}

/** «Скорость речи» of voice notes and calls (Persona.voice_speed). */
export const VOICE_SPEED = { min: 0.8, max: 1.5, step: 0.05, default: 1 } as const;

/** A slider value as a clean number in range, two decimals (no 1.0500000001 in a PATCH). */
export function normalizeSpeed(value: number): number {
  if (!Number.isFinite(value)) return VOICE_SPEED.default;
  const clamped = Math.min(Math.max(value, VOICE_SPEED.min), VOICE_SPEED.max);
  return Math.round(clamped * 100) / 100;
}

/** 1.3 -> «×1.30». */
export function formatSpeed(value: number): string {
  return `×${normalizeSpeed(value).toFixed(2)}`;
}
