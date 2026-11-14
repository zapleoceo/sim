import { editTarget, rulePlace, sourceLabel } from './persona-prompt-logic';

describe('persona prompt map logic', () => {
  it('sends every piece to the place where it is edited', () => {
    expect(editTarget({ source: 'persona', field: 'prohibitions' }, 3)).toEqual({ kind: 'anchor', anchor: 'persona-prohibitions' });
    expect(editTarget({ source: 'setting', field: 'output_contract' }, 3)).toEqual({ kind: 'anchor', anchor: 'setting-output_contract' });
    // the scenario is edited on the persona page, the rest of the strategy on its own page
    expect(editTarget({ source: 'strategy', field: 'prompt' }, 3)).toEqual({ kind: 'anchor', anchor: 'strategy-prompt' });
    expect(editTarget({ source: 'strategy', field: 'offer' }, 3)).toEqual({ kind: 'strategy', strategyId: 3 });
    // the single prompt has its own editor on the page; a piece fixed in the code has nothing to edit
    expect(editTarget({ source: 'persona', field: 'agent_prompt' }, 3)).toEqual({ kind: 'anchor', anchor: 'persona-agent_prompt' });
    expect(editTarget({ source: 'code', field: '' }, 3)).toBeNull();
  });

  it('names the source and the place of a fixed rule', () => {
    expect(sourceLabel('persona')).toBe('Поле персонажа');
    expect(sourceLabel('strategy')).toBe('Стратегия');
    expect(sourceLabel('setting')).toBe('Общая настройка');
    expect(sourceLabel('code')).toBe('Фиксировано в коде');
    expect(rulePlace('call')).toBe('к промпту на звонке');
    expect(rulePlace('video_note')).toBe('к каждому кружочку');
    expect(rulePlace('ai_photo')).toBe('к каждому фото ИИ');
    expect(rulePlace('media_tool')).toBe('к контексту каждого ответа');
    expect(rulePlace('guard_retry')).toBe('к повторному запросу после отказа охранника');
    expect(rulePlace('neutral_handoff')).toBe('в диалог вместо ответа, который дважды не прошёл проверку,');
    expect(rulePlace('followup')).toBe('к контексту напоминания');
    expect(rulePlace('new_rule')).toBe('кодом');
  });
});
