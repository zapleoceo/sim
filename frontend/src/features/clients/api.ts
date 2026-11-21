import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { qk } from '@/lib/query';
import type { Client, ClientDetail, ClientPatch, Items } from '@/lib/types';

export const clientsApi = {
  list: (q: string) => api.get<Items<Client>>('/clients', { params: { q } }),
  get: (id: number) => api.get<ClientDetail>(`/clients/${id}`),
  update: (id: number, patch: ClientPatch) => api.patch<Client>(`/clients/${id}`, patch),
};

/**
 * The one way to change a client (profile, notes, memory, AI switch): PATCH, then refresh the
 * clients and the chats (their rows show the client's name and AI state).
 */
export function useUpdateClient(id: number, successText: (patch: ClientPatch) => string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: ClientPatch) => clientsApi.update(id, patch),
    onSuccess: (_client, patch) => {
      void queryClient.invalidateQueries({ queryKey: qk.clients });
      void queryClient.invalidateQueries({ queryKey: qk.chats });
      toast.success(successText(patch));
    },
  });
}
