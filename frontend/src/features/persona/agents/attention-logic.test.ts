import { describe, expect, it } from 'vitest';
import { ATTENTION_MODES, DEFAULT_ATTENTION, clampPace, modeHint, paceLabel, withFlag } from './attention-logic';

describe('«Внимание» settings', () => {
  it('has three modes, each with a one-line hint', () => {
    expect(ATTENTION_MODES.map((m) => m.value)).toEqual(['human', 'free_only', 'instant']);
    expect(ATTENTION_MODES.map((m) => m.label)).toEqual(['Как живой человек', 'Только когда свободна', 'Всегда сразу']);
    for (const mode of ATTENTION_MODES) expect(modeHint(mode.value)).toBe(mode.hint);
  });

  it('keeps the pace in 0.5–2 by tenths and names it', () => {
    expect([clampPace(0.1), clampPace(1.2499), clampPace(1.55), clampPace(5), clampPace(Number.NaN)]).toEqual([0.5, 1.2, 1.6, 2, 1]);
    expect([paceLabel(0.5), paceLabel(1), paceLabel(1.6)]).toEqual(['быстро ×0.5', 'обычно ×1.0', 'неторопливо ×1.6']);
  });

  it('switches one flag and keeps the rest', () => {
    const next = withFlag(DEFAULT_ATTENTION, 'night_dnd', false);
    expect(next).toEqual({ ...DEFAULT_ATTENTION, night_dnd: false });
    expect(DEFAULT_ATTENTION.night_dnd).toBe(true);                       // not mutated
  });
});
