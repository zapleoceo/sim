import { useQuery } from '@tanstack/react-query';
import { Merge } from 'lucide-react';
import { useState } from 'react';
import { Avatar } from '@/components/avatar';
import { EmptyState } from '@/components/empty-state';
import { SearchInput } from '@/components/search-input';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog';
import { qk } from '@/lib/query';
import type { ClientDetail, Lead } from '@/lib/types';
import { clientName, cn } from '@/lib/utils';
import { leadsApi } from './api';
import { MessengerIcons, useMergeClients } from './client-matches';
import { mergeBlocker } from './merge-logic';

/**
 * «Объединить с…» in the lead card: find another lead of the same persona by name, @username or
 * phone and merge it into this card (the same confirmation as a suggested pair). A lead with an
 * account in one of this lead's messengers is shown but can not be picked, with the reason.
 */
export function MergeWithButton({ client }: { client: ClientDetail }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const { merge, pending } = useMergeClients();
  const leads = useQuery({
    queryKey: qk.clientList(q, client.persona_id, ''),
    queryFn: () => leadsApi.list({ q, personaId: client.persona_id, stage: '' }),
    enabled: open,
    placeholderData: (prev) => prev,
  });
  const others = (leads.data?.items ?? []).filter((lead) => lead.id !== client.id);

  const pick = async (lead: Lead): Promise<void> => {
    if (await merge(client, lead)) setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Merge />
          Объединить с…
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Объединить с другим лидом</DialogTitle>
          <DialogDescription>
            Найдите лида по имени, @username или телефону. Его чаты, память и паспорт перейдут в карточку «{clientName(client)}».
          </DialogDescription>
        </DialogHeader>
        <SearchInput value={q} onChange={setQ} label="Поиск лида" placeholder="Имя, username или телефон" />
        <ul aria-label="Лиды" className="-mx-2 flex max-h-80 flex-col overflow-y-auto scrollbar-thin">
          {others.map((lead) => {
            const blocker = mergeBlocker(client, lead);
            const name = clientName(lead);
            return (
              <li key={lead.id}>
                <button
                  type="button"
                  disabled={blocker !== null || pending}
                  onClick={() => void pick(lead)}
                  className={cn(
                    'flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                    blocker ? 'cursor-not-allowed opacity-60' : 'hover:bg-accent',
                  )}
                >
                  <Avatar name={name} size={32} />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="flex items-center gap-1.5 truncate text-sm font-medium">
                      {name}
                      <MessengerIcons kinds={lead.messengers} />
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {[lead.username && `@${lead.username}`, lead.phone].filter(Boolean).join(' · ') || 'без username и телефона'}
                    </span>
                    {blocker && <span className="text-xs text-warning">{blocker}</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {leads.isSuccess && others.length === 0 && (
          <EmptyState title={q ? `Никого не нашли по «${q}»` : 'Других лидов нет'} className="py-4" />
        )}
      </DialogContent>
    </Dialog>
  );
}
