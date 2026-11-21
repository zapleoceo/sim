import { useQuery } from '@tanstack/react-query';
import { ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { qk } from '@/lib/query';
import { personaApi } from './api';
import { MediaLibrarySection } from './media-library';
import { PersonaEditor } from './persona-editor';
import { ChannelSection, FormatRulesSection } from './readonly-sections';

const TOC: { id: string; label: string }[] = [
  { id: 'basics', label: 'Основное' },
  { id: 'prompt', label: 'Промт' },
  { id: 'models', label: 'Модели' },
  { id: 'voice', label: 'Голос' },
  { id: 'guard', label: 'Проверка' },
  { id: 'switches', label: 'Переключатели' },
  { id: 'format', label: 'Правила формата' },
  { id: 'channel', label: 'Канал' },
  { id: 'media', label: 'Медиатека' },
];

/** «Персонаж»: every persona setting on one page, plus the media library. */
export function PersonaPage() {
  const persona = useQuery({ queryKey: qk.persona, queryFn: personaApi.get });
  const models = useQuery({ queryKey: qk.models, queryFn: personaApi.models, staleTime: 5 * 60_000 });
  const voices = useQuery({ queryKey: qk.voices, queryFn: personaApi.voices, staleTime: Infinity });

  return (
    <Page width="wide" className="max-w-[1400px] pb-0 sm:pb-0">
      <PageHeader title={persona.data?.name ? `Персонаж: ${persona.data.name}` : 'Персонаж'} />
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
          {persona.isPending && (
            <div className="flex flex-col gap-4">
              <Skeleton className="h-48 w-full rounded-xl" />
              <Skeleton className="h-72 w-full rounded-xl" />
            </div>
          )}
          {persona.isError && <ErrorState title="Персонаж не загрузился" onRetry={() => void persona.refetch()} />}
          {persona.data && (
            <PersonaEditor
              persona={persona.data}
              models={models.data?.items ?? []}
              modelsError={models.isError ? `Каталог моделей недоступен: ${errorMessage(models.error)}` : undefined}
              voices={voices.data?.items ?? []}
            >
              <FormatRulesSection />
              <ChannelSection />
              <MediaLibrarySection />
            </PersonaEditor>
          )}
        </div>
      </div>
    </Page>
  );
}
