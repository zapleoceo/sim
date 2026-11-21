import { api } from '@/lib/api';
import type { Arc, ArcStatus, CanonFact, DayEvent, DaysResponse, Items, PersonaDay, PersonaLife } from '@/lib/types';

export interface ArcInput {
  title: string;
  summary: string;
  stage: string;
  status?: ArcStatus;
  ends_on?: string | null;
}

/** Her life on the dashboard (docs/api.md, «Жизнь персонажа»). */
export const lifeApi = {
  world: () => api.get<PersonaLife>('/persona/life'),
  saveWorld: (life: PersonaLife) => api.put<PersonaLife>('/persona/life', life),
  arcs: () => api.get<Items<Arc>>('/persona/arcs'),
  createArc: (arc: ArcInput) => api.post<Arc>('/persona/arcs', arc),
  updateArc: (id: number, patch: Partial<ArcInput>) => api.patch<Arc>(`/persona/arcs/${id}`, patch),
  deleteArc: (id: number) => api.delete(`/persona/arcs/${id}`),
  /** Yesterday … tomorrow of her local calendar, plus her today and minute of the day. */
  days: () => api.get<DaysResponse>('/persona/days'),
  saveDay: (day: string, events: DayEvent[]) => api.put<PersonaDay>(`/persona/days/${day}`, { events }),
  planDay: (day: string) => api.post<{ queued: boolean; day: string }>(`/persona/days/${day}/plan`),
  canon: () => api.get<Items<CanonFact>>('/persona/canon'),
  updateCanon: (id: number, patch: Partial<Pick<CanonFact, 'fact' | 'status'>>) =>
    api.patch<CanonFact>(`/persona/canon/${id}`, patch),
  describeAppearance: () => api.post<{ text: string; references: number }>('/persona/appearance/describe'),
};
