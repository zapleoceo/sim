import { api } from '@/lib/api';
import type { Arc, ArcStatus, CanonFact, DayEvent, DaysResponse, Items, PersonaDay, PersonaLife } from '@/lib/types';
import { personaPath } from '../api';

export interface ArcInput {
  title: string;
  summary: string;
  stage: string;
  status?: ArcStatus;
  ends_on?: string | null;
}

/** One persona's life on the dashboard (docs/api.md, «Жизнь персонажа»). */
export const lifeApi = {
  world: (id: number) => api.get<PersonaLife>(personaPath(id, '/life')),
  saveWorld: (id: number, life: PersonaLife) => api.put<PersonaLife>(personaPath(id, '/life'), life),
  arcs: (id: number) => api.get<Items<Arc>>(personaPath(id, '/arcs')),
  createArc: (id: number, arc: ArcInput) => api.post<Arc>(personaPath(id, '/arcs'), arc),
  updateArc: (id: number, arcId: number, patch: Partial<ArcInput>) =>
    api.patch<Arc>(personaPath(id, `/arcs/${arcId}`), patch),
  deleteArc: (id: number, arcId: number) => api.delete(personaPath(id, `/arcs/${arcId}`)),
  /** Yesterday … tomorrow of her local calendar, plus her today and minute of the day. */
  days: (id: number) => api.get<DaysResponse>(personaPath(id, '/days')),
  saveDay: (id: number, day: string, events: DayEvent[]) =>
    api.put<PersonaDay>(personaPath(id, `/days/${day}`), { events }),
  planDay: (id: number, day: string) =>
    api.post<{ queued: boolean; day: string }>(personaPath(id, `/days/${day}/plan`)),
  canon: (id: number) => api.get<Items<CanonFact>>(personaPath(id, '/canon')),
  updateCanon: (id: number, canonId: number, patch: Partial<Pick<CanonFact, 'fact' | 'status'>>) =>
    api.patch<CanonFact>(personaPath(id, `/canon/${canonId}`), patch),
  describeAppearance: (id: number) =>
    api.post<{ text: string; references: number }>(personaPath(id, '/appearance/describe')),
};
