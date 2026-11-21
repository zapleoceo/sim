import type { AttentionMode, AttentionSettings } from '@/lib/types';

/** The «Внимание» agent's key in the registry (docs/attention.md). */
export const ATTENTION_AGENT = 'attention';

/** The three modes with a one-line hint each. */
export const ATTENTION_MODES: readonly { value: AttentionMode; label: string; hint: string }[] = [
  {
    value: 'human',
    label: 'Как живой человек',
    hint: 'Берёт телефон, когда позволяет её день: занята — ответит позже, спит — утром.',
  },
  {
    value: 'free_only',
    label: 'Только когда свободна',
    hint: 'Занятость не мешает: отвечает как свободная. Сон и пробежка — по-прежнему без телефона.',
  },
  {
    value: 'instant',
    label: 'Всегда сразу',
    hint: 'Для тестов: отвечает сразу, как бот, без пауз и без её распорядка.',
  },
];

export const PACE = { min: 0.5, max: 2, step: 0.1 } as const;

export const DEFAULT_ATTENTION: AttentionSettings = {
  mode: 'human', pace: 1, may_leave_read: true, after_missed_call: true, night_dnd: true,
};

/** The pace within the slider's range, rounded to its step (tenths, without float noise). */
export function clampPace(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_ATTENTION.pace;
  const bounded = Math.min(Math.max(value, PACE.min), PACE.max);
  return Math.round(bounded * 10) / 10;
}

/** «быстро ×0.7» / «обычно ×1.0» / «неторопливо ×1.6». */
export function paceLabel(pace: number): string {
  const word = pace < 0.85 ? 'быстро' : pace <= 1.15 ? 'обычно' : 'неторопливо';
  return `${word} ×${pace.toFixed(1)}`;
}

/** The three on/off settings. */
export type AttentionFlag = 'may_leave_read' | 'after_missed_call' | 'night_dnd';

export function withFlag(settings: AttentionSettings, flag: AttentionFlag, on: boolean): AttentionSettings {
  const next = { ...settings };
  next[flag] = on;
  return next;
}

export const modeHint =(mode: AttentionMode): string => ATTENTION_MODES.find((m) => m.value === mode)?.hint ?? '';
