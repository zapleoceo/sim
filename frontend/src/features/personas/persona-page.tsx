import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import { Smile } from 'lucide-react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { BackLink, Page, PageHeader } from '@/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { personasApi } from '@/lib/api/personas';
import { strategiesApi } from '@/lib/api/strategies';
import { useAuth } from '@/lib/auth';
import type { Persona, PersonaIn } from '@/lib/models';
import { qk } from '@/lib/query';
import { positiveInt, upsertById } from '@/lib/utils';
import { AiVideoNote } from './ai-video-note';
import { PersonaEditor } from './persona-editor';
import { PersonaGallery } from './persona-gallery';
import { PersonaPreview } from './persona-preview';
import { BLOCKS, PersonaSections, Section } from './persona-sections';

/**
 * Persona page: `/personas/new` and `/personas/$id`. Everything of a persona in one place, in blocks that
 * fold («Профиль» and «Промпт» are open at first): her own data and her prompt (one form, one «Сохранить»),
 * then, once she is saved, her photos and video notes; the preview chat next to them. The page shows as soon
 * as the personas are loaded: strategies and Broker models fill in after.
 */
export function PersonaPage() {
  const params: { id?: string } = useParams({ strict: false });
  const isNew = params.id === undefined;
  const id = positiveInt(params.id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const ownerId = useAuth().user?.id ?? 0;

  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const strategies = useQuery({ queryKey: qk.strategies, queryFn: strategiesApi.list });
  // Auxiliary: an unavailable Broker gives a hint at the field, not a toast or a broken page.
  const models = useQuery({ queryKey: qk.models, queryFn: personasApi.models, retry: false });

  const persona = id === null ? null : (personas.data?.find((p) => p.id === id) ?? null);
  const missing = !isNew && personas.isSuccess && persona === null;

  const save = useMutation({
    mutationFn: (body: PersonaIn) => (persona ? personasApi.update(persona.id, body) : personasApi.create(body)),
    onSuccess: (saved: Persona) => {
      queryClient.setQueryData<Persona[]>(qk.personas, (list) => upsertById(list ?? [], saved));
      void queryClient.invalidateQueries({ queryKey: qk.personaPromptVersions(saved.id) });
      toast.success('Персонаж сохранён');
      if (persona?.id !== saved.id) {
        void navigate({ to: '/personas/$id', params: { id: String(saved.id) }, replace: true });
      }
    },
  });

  const remove = useMutation({
    mutationFn: (pid: number) => personasApi.remove(pid),
    onSuccess: (_, pid) => {
      queryClient.setQueryData<Persona[]>(qk.personas, (list) => (list ?? []).filter((p) => p.id !== pid));
      void navigate({ to: '/personas' });
    },
  });

  const askRemove = async (p: Persona): Promise<void> => {
    const ok = await confirm({
      title: 'Удалить персонажа?',
      message: `«${p.name}» будет удалён. Диалоги и каналы, привязанные к нему, могут перестать работать.`,
      confirmLabel: 'Удалить',
      danger: true,
    });
    if (ok) remove.mutate(p.id);
  };

  const title = persona ? persona.name : missing ? 'Персонаж' : 'Новый персонаж';

  return (
    <Page>
      <BackLink to="/personas">Все персонажи</BackLink>
      {personas.isError ? (
        <ErrorState onRetry={() => void personas.refetch()} />
      ) : personas.isPending ? (
        <div className="flex flex-col gap-4" aria-busy="true">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-[28rem] rounded-xl" />
        </div>
      ) : (
        <>
          <PageHeader title={title} subtitle={persona || missing ? undefined : 'Заполните карточку и сохраните'} />
          {missing ? (
            <EmptyState icon={Smile} title="Персонаж не найден" hint="Возможно, он был удалён." />
          ) : (
            <PersonaSections className="touch-40 grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
              <div data-slot="persona-blocks" className="flex min-w-0 flex-col gap-4">
                <PersonaEditor
                  key={persona?.id ?? 'new'}
                  persona={persona}
                  models={models.data ?? []}
                  modelsUnavailable={models.isError}
                  strategies={strategies.data ?? []}
                  saving={save.isPending}
                  onSave={(body) => save.mutate(body)}
                  onRemove={persona ? () => void askRemove(persona) : undefined}
                />
                {persona && (
                  <div className={BLOCKS}>
                    <Section id="media" hint="Её галерея и кружочек ИИ по запросу. Модель фото, пауза между медиа и получатель кружочков — в «Общих правилах».">
                      <PersonaGallery personaId={persona.id} />
                      <AiVideoNote personaId={persona.id} />
                    </Section>
                  </div>
                )}
              </div>
              {persona && (
                <div data-slot="persona-preview" className="min-w-0 rounded-xl border bg-card p-3 sm:p-5 xl:sticky xl:top-5">
                  <PersonaPreview
                    key={`${ownerId}:${persona.id}`}
                    ownerId={ownerId}
                    personaId={persona.id}
                    asyncEnabled={persona.preview_async_enabled}
                  />
                </div>
              )}
            </PersonaSections>
          )}
        </>
      )}
    </Page>
  );
}
