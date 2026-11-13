import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import { Smile } from 'lucide-react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { BackLink, Page, PageHeader } from '@/components/page';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { personasApi } from '@/lib/api/personas';
import { strategiesApi } from '@/lib/api/strategies';
import type { Persona, PersonaIn } from '@/lib/models';
import { qk } from '@/lib/query';
import { positiveInt, upsertById } from '@/lib/utils';
import { AiVideoNote } from './ai-video-note';
import { PersonaEditor } from './persona-editor';
import { PersonaGallery } from './persona-gallery';
import { PersonaPreview } from './persona-preview';
import { PersonaPromptMap } from './persona-prompt-map';
import { Section, SectionNav, SECTIONS, type SectionId } from './persona-sections';
import { PersonaShared } from './persona-shared';

/** Sections that need a saved persona: the shared settings act on real replies, the prompt map is of saved data. */
const SAVED_ONLY: readonly SectionId[] = ['shared', 'assembled'];

/**
 * Persona page: `/personas/new` and `/personas/$id`. Everything of a persona in one place: her own data
 * (one form), the shared instructions mixed into her replies, the assembled prompt with the source of every
 * piece; the gallery, the AI video note and the preview chat next to it once she is saved. The page shows as
 * soon as the personas are loaded: strategies, Broker models, settings and the prompt map fill in after.
 */
export function PersonaPage() {
  const params: { id?: string } = useParams({ strict: false });
  const isNew = params.id === undefined;
  const id = positiveInt(params.id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const confirm = useConfirm();

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
      void queryClient.invalidateQueries({ queryKey: qk.personaPrompt(saved.id) });
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
  const sections = SECTIONS.map((s) => s.id).filter((id) => persona !== null || !SAVED_ONLY.includes(id));

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
          <PageHeader title={title} subtitle={persona ? 'Все данные и весь промпт персонажа на одной странице' : missing ? undefined : 'Заполните карточку и сохраните'} />
          {missing ? (
            <EmptyState icon={Smile} title="Персонаж не найден" hint="Возможно, он был удалён." />
          ) : (
            <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_24rem]">
              <div className="flex min-w-0 flex-col gap-5">
                <SectionNav ids={sections} />
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
                  <>
                    <Card>
                      <Section id="shared" hint="Общие для всех персонажей: правка здесь меняет их везде. Те же поля есть на страницах «Общие правила» и «Агенты».">
                        <PersonaShared />
                      </Section>
                    </Card>
                    <Card>
                      <Section id="assembled" hint="Что получает модель: какой кусок откуда и где его поменять.">
                        <PersonaPromptMap personaId={persona.id} />
                      </Section>
                    </Card>
                  </>
                )}
              </div>
              {persona && (
                <div className="flex flex-col gap-5 xl:sticky xl:top-5">
                  <PersonaGallery personaId={persona.id} />
                  <AiVideoNote personaId={persona.id} />
                  <div className="rounded-xl border bg-card p-4 shadow-xs sm:p-5">
                    <PersonaPreview key={persona.id} personaId={persona.id} />
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Page>
  );
}
