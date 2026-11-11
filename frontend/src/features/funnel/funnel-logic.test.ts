import type { ClientMini, FunnelResponse } from '@/lib/models';
import { MOVE_TARGETS, buildColumns, moveInFunnel } from './funnel-logic';

const anna: ClientMini = { id: 7, display_name: 'Анна', avatar_url: '', stage: 'first_contact', status: 'active' };
const reply: FunnelResponse = {
  stages: [{ stage: 'first_contact', count: 3, clients: [anna] }],
  statuses: { active: 1, paused: 0, no_response: 0, refused: 0 },
};

describe('funnel logic', () => {
  it('builds all seven columns in stage order with partner-only ones locked', () => {
    const cols = buildColumns(reply);
    expect(cols.map((c) => c.stage)).toEqual([
      'first_contact', 'engaged', 'needs_identified', 'interested', 'handed_to_partner', 'registered', 'paid',
    ]);
    expect(cols[0]).toMatchObject({ count: 3, clients: [anna], locked: false });
    expect(cols[1]).toMatchObject({ count: 0, clients: [] });
    expect(cols.filter((c) => c.locked).map((c) => c.stage)).toEqual(['registered', 'paid']);
  });

  it('handles a missing reply', () => {
    expect(buildColumns(undefined).every((c) => c.count === 0)).toBe(true);
  });

  it('moves a client optimistically and keeps counts consistent', () => {
    const moved = moveInFunnel(reply, anna, 'first_contact', 'engaged');
    const cols = buildColumns(moved);
    expect(cols[0]).toMatchObject({ count: 2, clients: [] });
    expect(cols[1].count).toBe(1);
    expect(cols[1].clients[0]).toMatchObject({ id: 7, stage: 'engaged' });
    // the original reply is untouched (rollback snapshot)
    expect(reply.stages[0].clients).toEqual([anna]);
  });

  it('offers only manually reachable stages as move targets', () => {
    expect(MOVE_TARGETS.map((s) => s.id)).not.toContain('paid');
    expect(MOVE_TARGETS.map((s) => s.id)).not.toContain('registered');
  });
});
