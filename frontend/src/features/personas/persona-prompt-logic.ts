import type { PromptPart } from '@/lib/models';

/** Anchor of a persona form field on the persona page. */
export const personaFieldAnchor = (field: string): string => `persona-${field}`;
/** Anchor of a shared setting in «Общие инструкции» on the persona page. */
export const settingAnchor = (key: string): string => `setting-${key}`;
/** Anchor of the strategy scenario editor on the persona page. */
export const STRATEGY_PROMPT_ANCHOR = 'strategy-prompt';

/** Where a piece of the prompt is edited: a place on this page, or the strategy's own page. */
export type EditTarget = { kind: 'anchor'; anchor: string } | { kind: 'strategy'; strategyId: number };

/** `null`: the piece is fixed in the code, there is nothing to edit. */
export function editTarget(part: Pick<PromptPart, 'source' | 'field'>, strategyId: number): EditTarget | null {
  if (part.source === 'code') return null;
  if (part.source === 'persona') return { kind: 'anchor', anchor: personaFieldAnchor(part.field) };
  if (part.source === 'setting') return { kind: 'anchor', anchor: settingAnchor(part.field) };
  if (part.field === 'prompt') return { kind: 'anchor', anchor: STRATEGY_PROMPT_ANCHOR };
  return { kind: 'strategy', strategyId };
}

const SOURCE_LABELS: Record<PromptPart['source'], string> = {
  persona: 'Поле персонажа',
  strategy: 'Стратегия',
  setting: 'Общая настройка',
  code: 'Фиксировано в коде',
};

export const sourceLabel = (source: PromptPart['source']): string => SOURCE_LABELS[source];

/** What a fixed rule is attached to, for the «Добавляется … и не редактируется» caption. */
const RULE_PLACES: Record<string, string> = {
  call: 'к промпту на звонке',
  video_note: 'к каждому кружочку',
  ai_photo: 'к каждому фото ИИ',
  media_tool: 'к контексту каждого ответа',
  guard_retry: 'к повторному запросу после отказа охранника',
  neutral_handoff: 'в диалог вместо ответа, который дважды не прошёл проверку,',
  followup: 'к контексту напоминания',
};

export const rulePlace = (key: string): string => RULE_PLACES[key] ?? 'кодом';
