import { checkStageDrop } from './funnel-rules';
import { Stage } from './models';

describe('checkStageDrop', () => {
  const reachable: Stage[] = ['first_contact', 'engaged', 'needs_identified', 'interested', 'handed_to_partner'];

  it('allows moves between AI-reachable stages, forward and back', () => {
    for (const a of reachable) {
      for (const b of reachable) {
        if (a === b) continue;
        expect(checkStageDrop(a, b).ok).withContext(`${a} -> ${b}`).toBeTrue();
      }
    }
  });

  it('rejects drops into registered and paid with the partner-only message', () => {
    for (const to of ['registered', 'paid'] as const) {
      const v = checkStageDrop('interested', to);
      expect(v.ok).toBeFalse();
      if (!v.ok) expect(v.reason).toContain('подтверждается только данными партнёра');
    }
  });

  it('rejects moving a client out of partner-confirmed stages', () => {
    expect(checkStageDrop('paid', 'engaged').ok).toBeFalse();
    expect(checkStageDrop('registered', 'first_contact').ok).toBeFalse();
  });

  it('treats a drop on the same column as a silent no-op', () => {
    const v = checkStageDrop('engaged', 'engaged');
    expect(v.ok).toBeFalse();
    if (!v.ok) expect(v.reason).toBe('');
  });
});
