import { describe, expect, it } from 'vitest';
import { REGISTRY, SAVED } from './agents-fixtures';
import {
  DEFAULT_MODEL, MODEL_OFF, canSwitchOff, defaultModelLabel, defaultSettings, draftErrors, effectiveModel, isSubscriptionModel,
  modelToSelect, promptText, promptValue, selectToModel, toDraft, toPatch,
} from './agents-logic';
import { sameJson } from '@/lib/utils';

const specOf = (key: string) => REGISTRY.agents.find((a) => a.key === key)!;

describe('agents draft', () => {
  it('drops the effective model and compares by value', () => {
    const draft = toDraft(SAVED);
    expect(draft.agents.reply).toEqual({ model: 'gpt-text', key_tier: 'paid', prompt: '', max_tokens: 4096 });
    expect(draft.guard_rules).toBe('Не называть фамилию.');
    expect(sameJson(draft, toDraft(SAVED))).toBe(true);
    expect(sameJson(draft, { ...draft, guard_rules: '' })).toBe(false);
  });

  it('patches only the changed agents and always sends the rule list and «Внимание»', () => {
    const saved = toDraft(SAVED);
    const draft = { ...saved, agents: { ...saved.agents, guard: { ...saved.agents.guard!, key_tier: 'free' as const } } };
    expect(toPatch(draft, saved)).toEqual({
      agents: { guard: { model: null, key_tier: 'free', prompt: '', max_tokens: 400 } },
      guard_rules: 'Не называть фамилию.',
      attention: SAVED.attention,
    });
    expect(toPatch(saved, saved)).toEqual({ agents: {}, guard_rules: 'Не называть фамилию.', attention: SAVED.attention });
  });

  it('a changed «Внимание» setting makes the draft dirty', () => {
    const saved = toDraft(SAVED);
    expect(saved.attention).not.toBe(SAVED.attention);                  // a copy, not the cache object
    expect(sameJson({ ...saved, attention: { ...saved.attention, pace: 1.5 } }, saved)).toBe(false);
  });

  it('defaults of an agent', () => {
    expect(defaultSettings(specOf('guard'))).toEqual({ model: null, key_tier: 'paid', prompt: '', max_tokens: 400 });
    expect(defaultSettings(specOf('tts')).max_tokens).toBeNull();
  });
});

describe('models', () => {
  it('maps «by default» and «off» to select values and back', () => {
    expect([modelToSelect(null), modelToSelect(''), modelToSelect('x/y')]).toEqual([DEFAULT_MODEL, MODEL_OFF, 'x/y']);
    expect([selectToModel(DEFAULT_MODEL), selectToModel(MODEL_OFF), selectToModel('x/y')]).toEqual([null, '', 'x/y']);
  });

  it('only an agent with a default model can be switched off', () => {
    expect(canSwitchOff(specOf('life'))).toBe(true);
    expect(canSwitchOff(specOf('guard'))).toBe(false);
  });

  it('resolves the effective model like the backend: own, default, fallback', () => {
    const draft = toDraft(SAVED);
    const specs = REGISTRY.agents;
    expect(effectiveModel(draft, specs, 'guard')).toBe('gpt-text');
    expect(effectiveModel(draft, specs, 'life')).toBe('codex-sub/gpt-6.1-sol');
    const off = { ...draft, agents: { ...draft.agents, life: { ...draft.agents.life!, model: '' } } };
    expect(effectiveModel(off, specs, 'life')).toBe('');
    const empty = { ...draft, agents: { ...draft.agents, guard: { ...draft.agents.guard!, model: '' } } };
    expect(effectiveModel(empty, specs, 'guard')).toBe('gpt-text');           // '' without a default = by default
    const noReply = { ...draft, agents: { ...draft.agents, reply: { ...draft.agents.reply!, model: null } } };
    expect(effectiveModel(noReply, specs, 'memory')).toBe('');
    expect(effectiveModel(draft, specs, 'unknown')).toBe('');
  });

  it('labels «by default» by the default model or the fallback agent', () => {
    expect(defaultModelLabel(specOf('life'), REGISTRY.agents)).toBe('По умолчанию: codex-sub/gpt-6.1-sol');
    expect(defaultModelLabel(specOf('guard'), REGISTRY.agents)).toBe('Как у агента «Собеседник»');
    expect(defaultModelLabel(specOf('tts'), REGISTRY.agents)).toBe('— не выбрана —');
    expect(isSubscriptionModel('codex-sub/gpt-6.1-sol')).toBe(true);
    expect(isSubscriptionModel('gemini/flash')).toBe(false);
  });
});

describe('prompts and limits', () => {
  it('shows the default prompt and stores only the owner’s own text', () => {
    const guard = specOf('guard');
    expect(promptText({ ...defaultSettings(guard) }, guard)).toBe('Проверь ответ {name}.');
    expect(promptText({ ...defaultSettings(guard), prompt: 'Свой' }, guard)).toBe('Свой');
    expect(promptValue('Проверь ответ {name}.  ', guard)).toBe('');
    expect(promptValue('   ', guard)).toBe('');
    expect(promptValue('Свой {name}', guard)).toBe('Свой {name}');
  });

  it('max_tokens must be an integer in the Broker range where it is tunable', () => {
    const draft = toDraft(SAVED);
    expect(draftErrors(draft, REGISTRY)).toEqual([]);
    const bad = { ...draft, agents: { ...draft.agents, guard: { ...draft.agents.guard!, max_tokens: 10 },
      reply: { ...draft.agents.reply!, max_tokens: 1.5 }, tts: { ...draft.agents.tts!, max_tokens: 5 } } };
    expect(draftErrors(bad, REGISTRY)).toEqual([
      'Собеседник: max_tokens — целое число от 64 до 16384',
      'Охранник: max_tokens — целое число от 64 до 16384',
    ]);
    const cleared = { ...draft, agents: { ...draft.agents, guard: { ...draft.agents.guard!, max_tokens: null } } };
    expect(draftErrors(cleared, REGISTRY)).toEqual([]);
  });
});
