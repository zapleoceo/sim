import type { PromptPart } from '@/lib/models';
import { settingAnchor, settingPage, type SettingPage } from '../settings/settings-logic';

export { settingAnchor };

/** Anchor of a persona form field on the persona page. */
export const personaFieldAnchor = (field: string): string => `persona-${field}`;
/** Anchor of the strategy scenario editor on the persona page. */
export const STRATEGY_PROMPT_ANCHOR = 'strategy-prompt';

/** Where a piece of the prompt is edited: a place on this page, a shared setting on its own page,
 * or the strategy's page. */
export type EditTarget =
  | { kind: 'anchor'; anchor: string }
  | { kind: 'setting'; page: SettingPage; anchor: string }
  | { kind: 'strategy'; strategyId: number };

export function editTarget(part: Pick<PromptPart, 'source' | 'field'>, strategyId: number): EditTarget | null {
  if (part.source === 'code') return null;
  if (part.source === 'persona') return { kind: 'anchor', anchor: personaFieldAnchor(part.field) };
  if (part.source === 'setting') return { kind: 'setting', page: settingPage(part.field), anchor: settingAnchor(part.field) };
  if (part.field === 'prompt') return { kind: 'anchor', anchor: STRATEGY_PROMPT_ANCHOR };
  return { kind: 'strategy', strategyId };
}

const SOURCE_LABELS: Record<PromptPart['source'], string> = {
  code: 'Правило кода',
  persona: 'Промпт персонажа',
  strategy: 'Стратегия',
  setting: 'Общая настройка',
};

export const sourceLabel = (source: PromptPart['source']): string => SOURCE_LABELS[source];

/** What a fixed rule is attached to, for the «Добавляется … и не редактируется» caption. */
const RULE_PLACES: Record<string, string> = {
  call: 'к промпту на звонке',
  media_tool: 'к контексту каждого ответа',
};

export const rulePlace = (key: string): string => RULE_PLACES[key] ?? 'кодом';
