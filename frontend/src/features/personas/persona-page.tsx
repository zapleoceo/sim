import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { Smile } from 'lucide-react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { BackLink, Page, PageHeader } from '@/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { personasApi } from '@/lib/api/personas';
import { useAuth } from '@/lib/auth';
import { strategiesApi } from '@/lib/api/strategies';
import type { Persona, PersonaIn } from '@/lib/models';
import { qk } from '@/lib/query';
import { positiveInt, upsertById } from '@/lib/utils';
import { AiVideoNote } from './ai-video-note';
import { PersonaAgentPrompt } from './persona-agent-prompt';
import { PersonaEditor } from './persona-editor';
import { PersonaGallery } from './persona-gallery';
import { PersonaPreview } from './persona-preview';
import { settingAnchor } from './persona-prompt-logic';
import { PersonaPromptMap } from './persona-prompt-map';
import { BLOCKS, PersonaSections, Section } from './persona-sections';

/**
 * Persona page: `/personas/new` and `/personas/$id`. Only what belongs to this persona, in blocks that
 * fold (only «Профиль» is open at first): her own data (one form), then, once she is saved, her prompt
 * with its versions, her photos and video notes and the assembled prompt with the source of every piece
 * (shared pieces link to «Общие правила» / «Помощники», where they are edited); the preview chat next to them. The page shows as soon as the personas are loaded: strategies,
 * Broker models, settings and the prompt map fill in after.
 */
export function PersonaPage() {
  const { isOwner } = useAuth();
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
                    <Section id="prompt" hint="Единый текст, который модель получает как есть. Формат ответа, контекст и проверка добавляются кодом.">
                      {isOwner && <PersonaAgentPrompt key={persona.id} personaId={persona.id} />}
                    </Section>
                  </div>
                )}
                {persona && (
                  <div className={BLOCKS}>
                    <Section id="media" hint="Её галерея и кружочек ИИ по запросу.">
                      <PersonaGallery personaId={persona.id} />
                      <AiVideoNote personaId={persona.id} />
                      <Link
                        to="/settings"
                        hash={settingAnchor('persona_photo_model')}
                        className="inline-flex w-fit items-center text-xs font-medium text-primary underline-offset-4 hover:underline max-md:min-h-[40px]"
                      >
                        Модель фото, пауза между медиа и получатель кружочков — в «Общих правилах»
                      </Link>
                    </Section>
                    <Section id="assembled" hint="Что получает модель: какой кусок откуда и где его поменять.">
                      <PersonaPromptMap personaId={persona.id} />
                    </Section>
                  </div>
                )}
              </div>
              {persona && (
                <div data-slot="persona-preview" className="min-w-0 rounded-xl border bg-card p-3 sm:p-5 xl:sticky xl:top-5">
                  <PersonaPreview key={persona.id} personaId={persona.id} />
                </div>
              )}
            </PersonaSections>
          )}
        </>
      )}
    </Page>
  );
}
