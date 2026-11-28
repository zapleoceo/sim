import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { qk } from '@/lib/query';
import type {
  ChatDay,
  ChatDayPatch,
  ChatDaysResponse,
  Client,
  ClientDetail,
  ClientMatch,
  ClientPatch,
  ClientTimelineItem,
  Items,
  Lead,
  LeadStage,
} from '@/lib/types';

export const CLIENT_TIMELINE_PAGE = 50;

export interface LeadFilters {
  q: string;
  /** 0 = every persona. */
  personaId: number;
  /** '' = every stage. */
  stage: LeadStage | '';
}

export const leadsApi = {
  list: ({ q, personaId, stage }: LeadFilters) =>
    api.get<Items<Lead>>('/clients', { params: { q, persona_id: personaId || null, stage: stage || null } }),
  get: (id: number) => api.get<ClientDetail>(`/clients/${id}`),
  update: (id: number, patch: ClientPatch) => api.patch<Client>(`/clients/${id}`, patch),
  /** «Возможно, один человек»: every pair, or (`id`) the pairs of one client (docs/leads.md). */
  matches: (id = 0) => api.get<Items<ClientMatch>>(id ? `/clients/${id}/matches` : '/clients/matches'),
  /** Merge `otherId` into `id`: its chats, memory and passport move here, it disappears. */
  merge: (id: number, otherId: number) => api.post<ClientDetail>(`/clients/${id}/merge`, { other_id: otherId }),
  /** «Это разные люди»: the pair is not suggested again. */
  distinct: (id: number, otherId: number) => api.post<void>(`/clients/${id}/distinct`, { other_id: otherId }),
  /** The journal of every chat of the client, newest first; `beforeId` pages back. */
  timeline: (id: number, beforeId?: number | null) =>
    api.get<Items<ClientTimelineItem>>(`/clients/${id}/timeline`, { params: { before_id: beforeId, limit: CLIENT_TIMELINE_PAGE } }),
};

/** One chat's day memory (docs/memory.md, docs/api.md «Дни»). */
export const daysApi = {
  list: (chatId: number) => api.get<ChatDaysResponse>(`/chats/${chatId}/days`),
  save: (chatId: number, day: string, patch: ChatDayPatch) => api.put<ChatDay>(`/chats/${chatId}/days/${day}`, patch),
  rebuild: (chatId: number, day: string) =>
    api.post<{ queued: boolean; day: string }>(`/chats/${chatId}/days/${day}/rebuild`),
  refreshPassport: (chatId: number) => api.post<{ queued: boolean }>(`/chats/${chatId}/passport/refresh`),
};

/**
 * The one way to change a lead (profile, notes, passport, AI switch): PATCH, then refresh the
 * leads and the chats (their rows show the client's name and AI state).
 */
export function useUpdateClient(id: number, successText: (patch: ClientPatch) => string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: ClientPatch) => leadsApi.update(id, patch),
    onSuccess: (_client, patch) => {
      void queryClient.invalidateQueries({ queryKey: qk.clients });
      void queryClient.invalidateQueries({ queryKey: qk.chats });
      toast.success(successText(patch));
    },
  });
}
