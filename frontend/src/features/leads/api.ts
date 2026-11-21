import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { qk } from '@/lib/query';
import type { Client, ClientDetail, ClientPatch, Items, Lead, LeadStage } from '@/lib/types';

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
