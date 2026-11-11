import type { ClientMini, FunnelResponse, Stage } from '@/lib/models';
import { STAGES, type StageMeta } from '@/lib/stages';

export interface FunnelColumn {
  stage: Stage;
  label: string;
  color: string;
  /** Registration / payment: set only by partner data, no manual moves in or out. */
  locked: boolean;
  count: number;
  clients: ClientMini[];
}

/** Stages a client can be moved to by hand (menu targets). */
export const MOVE_TARGETS: readonly StageMeta[] = STAGES.filter((s) => s.aiReachable);

/** Board columns in canonical stage order; stages missing in the reply are empty. */
export function buildColumns(f: FunnelResponse | undefined): FunnelColumn[] {
  const byStage = new Map((f?.stages ?? []).map((s) => [s.stage, s]));
  return STAGES.map((s) => {
    const src = byStage.get(s.id);
    return {
      stage: s.id,
      label: s.label,
      color: s.color,
      locked: !s.aiReachable,
      count: src?.count ?? 0,
      clients: src?.clients ?? [],
    };
  });
}

/** Optimistic move: the client leaves `from` and is put first in `to`; counts follow. */
export function moveInFunnel(f: FunnelResponse, client: ClientMini, from: Stage, to: Stage): FunnelResponse {
  const stages = STAGES.map((s) => f.stages.find((x) => x.stage === s.id) ?? { stage: s.id, count: 0, clients: [] });
  return {
    ...f,
    stages: stages.map((c) => {
      if (c.stage === from) {
        return { ...c, count: Math.max(0, c.count - 1), clients: c.clients.filter((x) => x.id !== client.id) };
      }
      if (c.stage === to) {
        return { ...c, count: c.count + 1, clients: [{ ...client, stage: to }, ...c.clients.filter((x) => x.id !== client.id)] };
      }
      return c;
    }),
  };
}
