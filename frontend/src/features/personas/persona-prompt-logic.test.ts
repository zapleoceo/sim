import { editTarget, rulePlace, sourceLabel } from './persona-prompt-logic';

describe('persona prompt map logic', () => {
  it('sends every piece to the place where it is edited', () => {
    expect(editTarget({ source: 'persona', field: 'prohibitions' }, 3)).toEqual({ kind: 'anchor', anchor: 'persona-prohibitions' });
    expect(editTarget({ source: 'setting', field: 'output_contract' }, 3)).toEqual({ kind: 'anchor', anchor: 'setting-output_contract' });
    // the scenario is edited on the persona page, the rest of the strategy on its own page
    expect(editTarget({ source: 'strategy', field: 'prompt' }, 3)).toEqual({ kind: 'anchor', anchor: 'strategy-prompt' });
    expect(editTarget({ source: 'strategy', field: 'offer' }, 3)).toEqual({ kind: 'strategy', strategyId: 3 });
  });

  it('names the source and the place of a fixed rule', () => {
    expect(sourceLabel('persona')).toBe('Поле персонажа');
    expect(sourceLabel('strategy')).toBe('Стратегия');
    expect(sourceLabel('setting')).toBe('Общая настройка');
    expect(rulePlace('call')).toBe('к промпту на звонке');
    expect(rulePlace('video_note')).toBe('к каждому кружочку');
    expect(rulePlace('ai_photo')).toBe('к каждому фото ИИ');
    expect(rulePlace('media_tool')).toBe('к контексту каждого ответа');
    expect(rulePlace('new_rule')).toBe('кодом');
  });
});
