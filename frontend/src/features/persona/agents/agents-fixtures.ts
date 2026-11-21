import type { AgentRegistry, AgentSpec, BrokerModel, PersonaAgents } from '@/lib/types';

/** Test data shaped like GET /agents/registry and GET /personas/{id}/agents (sim/domain/agents.py). */
const TEXT = [{ input: ['text'], output: ['text'], capabilities: [] }];

const spec = (key: string, title: string, group: string, extra: Partial<AgentSpec> = {}): AgentSpec => ({
  key, title, group, description: `${title} работает`, port: 'chat', model_filter: TEXT, key_tier: 'paid', model: '',
  fallback: null, prompt: null, readonly_prompt: null, placeholders: [], max_tokens: null, ...extra,
});

export const REGISTRY: AgentRegistry = {
  groups: [
    { key: 'reply', title: 'Ответ', description: 'Должны отвечать быстро.', agents: ['attention', 'reply', 'guard', 'tts'] },
    { key: 'background', title: 'Фон', description: 'Ответа никто не ждёт.', agents: ['life', 'memory'] },
  ],
  agents: [
    spec('attention', 'Внимание', 'reply', {
      model: 'gemini/gemini-3.5-flash-lite', prompt: 'Ты внимание {name}.', placeholders: ['name'], max_tokens: 1500,
    }),
    spec('reply', 'Собеседник', 'reply', { max_tokens: 4096, readonly_prompt: '# Как отвечать' }),
    spec('guard', 'Охранник', 'reply', {
      fallback: 'reply', prompt: 'Проверь ответ {name}.', placeholders: ['name', 'language'], max_tokens: 400,
    }),
    spec('tts', 'Голос', 'reply', { port: 'speech', model_filter: [{ input: [], output: ['audio'], capabilities: [] }] }),
    spec('life', 'Планировщик', 'background', {
      key_tier: 'auto', model: 'codex-sub/gpt-6.1-sol', prompt: 'План дня для {name}.', placeholders: ['name'], max_tokens: 6000,
    }),
    spec('memory', 'Летописец', 'background', { key_tier: 'auto', fallback: 'reply', prompt: 'Сводка.', max_tokens: 4096 }),
  ],
  key_tiers: [
    { value: 'auto', label: 'Авто' },
    { value: 'paid', label: 'Платный' },
    { value: 'free', label: 'Бесплатный' },
  ],
  max_tokens_range: { min: 64, max: 16384 },
};

export const SAVED: PersonaAgents = {
  agents: {
    attention: {
      model: null, key_tier: 'paid', prompt: '', max_tokens: 1500, effective_model: 'gemini/gemini-3.5-flash-lite',
    },
    reply: { model: 'gpt-text', key_tier: 'paid', prompt: '', max_tokens: 4096, effective_model: 'gpt-text' },
    guard: { model: null, key_tier: 'paid', prompt: '', max_tokens: 400, effective_model: 'gpt-text' },
    tts: { model: 'retired-tts', key_tier: 'paid', prompt: '', max_tokens: null, effective_model: 'retired-tts' },
    life: { model: null, key_tier: 'auto', prompt: '', max_tokens: 6000, effective_model: 'codex-sub/gpt-6.1-sol' },
    memory: { model: null, key_tier: 'auto', prompt: '', max_tokens: 4096, effective_model: 'gpt-text' },
  },
  guard_rules: 'Не называть фамилию.',
  attention: { mode: 'human', pace: 1, may_leave_read: true, after_missed_call: true, night_dnd: true },
};

export const MODELS: BrokerModel[] = [
  { id: 'gpt-text', available: true, input_modalities: ['text'], output_modalities: ['text'], capabilities: [] },
  { id: 'cheap-text', available: false, input_modalities: ['text'], output_modalities: ['text'], capabilities: [] },
  { id: 'gemini-tts', available: true, input_modalities: ['text'], output_modalities: ['audio'], capabilities: [] },
];
