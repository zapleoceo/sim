import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, useNavigate } from '@tanstack/react-router';
import { Archive, ArchiveRestore, Copy } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Avatar } from '@/components/avatar';
import { useConfirm } from '@/components/confirm';
import { ErrorState } from '@/components/empty-state';
import { BackLink, Page } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { qk } from '@/lib/query';
import type { Persona } from '@/lib/types';
import { positiveInt } from '@/lib/utils';
import { personaApi } from './api';
import { AgentsSection } from './agents/agents-section';
import { ChannelSection } from './channel-section';
import { CloneDialog } from './clone-dialog';
import { LifeSection } from './life/life-section';
import { MediaLibrarySection } from './media-library';
import { PersonaEditor } from './persona-editor';
import { PersonaIdProvider } from './persona-scope';
import { avatarUrl } from './personas-page';

const route = getRouteApi('/_app/personas/$personaId');

const TOC: { id: string; label: string }[] = [
  { id: 'basics', label: 'Основное' },
  { id: 'prompt', label: 'Промт' },
  { id: 'appearance', label: 'Внешность' },
  { id: 'voice', label: 'Голос' },
  { id: 'switches', label: 'Переключатели' },
  { id: 'agents', label: 'Агенты' },
  { id: 'life', label: 'Жизнь' },
  { id: 'channel', label: 'Канал' },
  { id: 'media', label: 'Медиатека' },
];

/** Name, archive state and the two persona-wide actions: «Клонировать» and archive / restore. */
function PersonaHeader({ persona }: { persona: Persona }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const [cloning, setCloning] = useState(false);
  const archive = useMutation({
    mutationFn: (isActive: boolean) => personaApi.setActive(persona.id, isActive),
    onSuccess: (saved) => {
      queryClient.setQueryData(qk.persona(saved.id), saved);
      void queryClient.invalidateQueries({ queryKey: qk.personaList });
      toast.success(saved.is_active ? 'Персонаж снова работает' : 'Персонаж в архиве: никому не отвечает');
    },
  });
  return (
    <header className="flex flex-wrap items-center gap-3">
      <Avatar name={persona.name} url={avatarUrl(persona.avatar_media_id)} size={44} />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <h1 className="truncate text-lg font-semibold sm:text-2xl">{persona.name}</h1>
        {!persona.is_active && <Badge variant="muted">в архиве</Badge>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => setCloning(true)}>
          <Copy />
          Клонировать
        </Button>
        {persona.is_active ? (
          <Button
            variant="ghost-destructive"
            size="sm"
            disabled={archive.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: `Убрать «${persona.name}» в архив?`,
                message: 'Она перестанет отвечать всем клиентам, а её Telegram выйдет из сети. Ничего не удаляется — вернуть можно в любой момент.',
                confirmLabel: 'В архив',
                danger: true,
              });
              if (ok) archive.mutate(false);
            }}
          >
            <Archive />
            В архив
          </Button>
        ) : (
          <Button variant="outline" size="sm" disabled={archive.isPending} onClick={() => archive.mutate(true)}>
            <ArchiveRestore />
            Вернуть из архива
          </Button>
        )}
      </div>
      <CloneDialog
        source={persona}
        open={cloning}
        onOpenChange={setCloning}
        onCloned={(clone) => void navigate({ to: '/personas/$personaId', params: { personaId: String(clone.id) } })}
      />
    </header>
  );
}

/** One persona: every setting on one page, her life, channel and media library. */
export function PersonaPage() {
  const { personaId } = route.useParams();
  const id = positiveInt(personaId) ?? 0;
  const persona = useQuery({ queryKey: qk.persona(id), queryFn: () => personaApi.get(id), enabled: id > 0 });
  const voices = useQuery({ queryKey: qk.voices, queryFn: personaApi.voices, staleTime: Infinity });

  return (
    <Page width="wide" className="max-w-[1400px] pb-0 sm:pb-0">
      <BackLink to="/personas">Все персонажи</BackLink>
      {id === 0 && <ErrorState title="Персонаж не найден" />}
      {persona.isError && <ErrorState title="Персонаж не загрузился" onRetry={() => void persona.refetch()} />}
      {id > 0 && persona.isPending && (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-12 w-64" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
      )}
      {persona.data && (
        // `key`: drafts of one persona never leak into another's page.
        <PersonaIdProvider id={persona.data.id} key={persona.data.id}>
          <PersonaHeader persona={persona.data} />
          <div className="grid gap-6 xl:grid-cols-[180px_minmax(0,1fr)]">
            <nav aria-label="Разделы страницы" className="hidden xl:block">
              <ul className="sticky top-6 flex flex-col gap-0.5 text-sm">
                {TOC.map((t) => (
                  <li key={t.id}>
                    <a href={`#${t.id}`} className="block rounded-md px-2.5 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                      {t.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
            <div className="flex min-w-0 flex-col gap-4">
              <PersonaEditor persona={persona.data} voices={voices.data?.items ?? []}>
                <AgentsSection />
                <LifeSection />
                <ChannelSection />
                <MediaLibrarySection />
              </PersonaEditor>
            </div>
          </div>
        </PersonaIdProvider>
      )}
    </Page>
  );
}
