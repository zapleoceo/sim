import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { qk } from '@/lib/query';
import type { PersonaRow } from '@/lib/types';
import { personaApi } from './api';

/**
 * Every persona (`GET /personas`), one query for the whole dashboard: the «Персонажи» table, the
 * persona filters of chats and leads, the names taken for «Клонировать».
 */
export function usePersonaList() {
  const query = useQuery({ queryKey: qk.personaList, queryFn: personaApi.list, staleTime: 60_000 });
  const rows: PersonaRow[] = useMemo(() => query.data?.items ?? [], [query.data]);
  return { query, rows };
}
