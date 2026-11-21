import type { AgentRegistry, AgentSettings, AgentSpec, PersonaAgents, PersonaAgentsPatch } from '@/lib/types';

/** The draft of the «Агенты» section: every agent's settings, the guard's rule list and the
 * «Внимание» settings. */
export type AgentsDraft = PersonaAgentsPatch;

/** Select values that are not model ids. */
export const DEFAULT_MODEL = '';
export const MODEL_OFF = '__off__';

const SUBSCRIPTION_PREFIXES = ['claude-sub/', 'codex-sub/'];

export function toDraft(saved: PersonaAgents): AgentsDraft {
  const agents: Record<string, AgentSettings> = {};
  for (const [key, { model, key_tier, prompt, max_tokens }] of Object.entries(saved.agents)) {
    agents[key] = { model, key_tier, prompt, max_tokens };
  }
  return { agents, guard_rules: saved.guard_rules, attention: { ...saved.attention } };
}

export function sameDraft(a: AgentsDraft, b: AgentsDraft): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The settings an agent has «by default» (what the reset endpoint returns). */
export function defaultSettings(spec: AgentSpec): AgentSettings {
  return { model: null, key_tier: spec.key_tier, prompt: '', max_tokens: spec.max_tokens };
}

/** Model as a select value: null → DEFAULT_MODEL, '' → MODEL_OFF. */
export function modelToSelect(model: string | null): string {
  if (model === null) return DEFAULT_MODEL;
  return model === '' ? MODEL_OFF : model;
}

export function selectToModel(value: string): string | null {
  if (value === DEFAULT_MODEL) return null;
  return value === MODEL_OFF ? '' : value;
}

/** Can the owner switch this agent off? Only where «by default» means a model. */
export const canSwitchOff = (spec: AgentSpec): boolean => spec.model !== '';

/** The model the agent will use with this draft: own choice, default, then the fallback chain. */
export function effectiveModel(draft: AgentsDraft, specs: readonly AgentSpec[], key: string, seen: ReadonlySet<string> = new Set()): string {
  const spec = specs.find((s) => s.key === key);
  if (!spec) return '';
  const own = draft.agents[key]?.model ?? null;
  const model = own === null || (own === '' && spec.model === '') ? spec.model : own.trim();
  if (!model && spec.fallback && !seen.has(spec.fallback)) {
    return effectiveModel(draft, specs, spec.fallback, new Set([...seen, key]));
  }
  return model;
}

export const isSubscriptionModel = (model: string): boolean => SUBSCRIPTION_PREFIXES.some((p) => model.startsWith(p));

/** What «by default» means for the model select of this agent. */
export function defaultModelLabel(spec: AgentSpec, specs: readonly AgentSpec[]): string {
  if (spec.model) return `По умолчанию: ${spec.model}`;
  const fallback = specs.find((s) => s.key === spec.fallback);
  return fallback ? `Как у агента «${fallback.title}»` : '— не выбрана —';
}

/** Problems that block saving, one line each (max_tokens out of the Broker's range). */
export function draftErrors(draft: AgentsDraft, registry: AgentRegistry): string[] {
  const { min, max } = registry.max_tokens_range;
  const errors: string[] = [];
  for (const spec of registry.agents) {
    const tokens = draft.agents[spec.key]?.max_tokens;
    if (spec.max_tokens !== null && tokens !== null && tokens !== undefined
        && (!Number.isInteger(tokens) || tokens < min || tokens > max)) {
      errors.push(`${spec.title}: max_tokens — целое число от ${min} до ${max}`);
    }
  }
  return errors;
}

/** The text shown in a prompt editor: the owner's prompt, else the default one. */
export const promptText = (settings: AgentSettings, spec: AgentSpec): string => settings.prompt || spec.prompt || '';

/** The stored prompt for an edited text: the default text (or nothing) means «by default». */
export function promptValue(text: string, spec: AgentSpec): string {
  return text.trim() === '' || text.trim() === (spec.prompt ?? '').trim() ? '' : text;
}

/** Only the agents that differ from the saved ones, plus the rule list and the «Внимание»
 * settings (one PUT). */
export function toPatch(draft: AgentsDraft, saved: AgentsDraft): PersonaAgentsPatch {
  const agents: Record<string, AgentSettings> = {};
  for (const [key, settings] of Object.entries(draft.agents)) {
    if (JSON.stringify(settings) !== JSON.stringify(saved.agents[key])) agents[key] = settings;
  }
  return { agents, guard_rules: draft.guard_rules, attention: draft.attention };
}
