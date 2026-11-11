import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Camera, Clapperboard, Film, Images, Plus, Video, type LucideIcon } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useIsPhone } from '@/hooks/use-media-query';
import { channelsApi } from '@/lib/api/channels';
import { contentApi } from '@/lib/api/content';
import { personasApi } from '@/lib/api/personas';
import { useAuth } from '@/lib/auth';
import type { ContentKind, ContentPost, ContentStatus } from '@/lib/models';
import { qk } from '@/lib/query';
import { cn, upsertById } from '@/lib/utils';
import { ContentEditor } from './content-editor';
import { contentColumns } from './content-logic';

const KIND_ICONS: Record<ContentKind, LucideIcon> = { photo: Camera, video: Video, reel: Film, story: Clapperboard };

type Selection = ContentPost | 'new' | null;

export function ContentPage() {
  const queryClient = useQueryClient();
  const isPhone = useIsPhone();
  const { isOwner } = useAuth();
  const [selected, setSelected] = useState<Selection>(null);
  const [editorKey, setEditorKey] = useState(0);
  const [tab, setTab] = useState<ContentStatus>('draft');

  const posts = useQuery({ queryKey: qk.content, queryFn: contentApi.list });
  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const channels = useQuery({ queryKey: qk.channels, queryFn: channelsApi.list });
  const personaList = personas.data ?? [];
  const igChannels = (channels.data ?? []).filter((c) => c.kind === 'instagram');

  const columns = contentColumns(posts.data ?? [], personaList);
  const active = columns.find((c) => c.status === tab) ?? columns[0];
  const current = selected && selected !== 'new' ? selected : null;

  const open = (sel: Exclude<Selection, null>): void => {
    setSelected(sel);
    setEditorKey((k) => k + 1);
  };

  const onSaved = (p: ContentPost, quiet = false): void => {
    queryClient.setQueryData<ContentPost[]>(qk.content, (list) => upsertById(list ?? [], p, true));
    if (current?.status === 'draft' && p.status === 'approved') toast.success('Пост одобрен');
    else if (!quiet) toast.success('Сохранено');
    setSelected(p);
  };

  const onDeleted = (id: number): void => {
    queryClient.setQueryData<ContentPost[]>(qk.content, (list) => list?.filter((x) => x.id !== id));
    setSelected(null);
  };

  const editor = selected !== null && (
    <ContentEditor
      key={editorKey}
      post={current}
      personas={personaList}
      igChannels={igChannels}
      canApprove={isOwner}
      onSaved={onSaved}
      onDeleted={onDeleted}
      onClose={() => setSelected(null)}
    />
  );

  return (
    <Page>
      <PageHeader title="Контент Instagram" subtitle="План публикаций: черновик, одобрение, расписание">
        <Button onClick={() => open('new')}>
          <Plus />
          Новый пост
        </Button>
      </PageHeader>

      <div className={cn('grid items-start gap-4', selected !== null && !isPhone && 'lg:grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="min-w-0">
          {posts.isError ? (
            <ErrorState onRetry={() => void posts.refetch()} />
          ) : posts.isPending ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-28 rounded-xl" />
              ))}
            </div>
          ) : posts.data.length === 0 ? (
            <EmptyState icon={Images} title="Постов пока нет" hint="Создайте черновик, сгенерируйте подпись и одобрите публикацию." />
          ) : (
            active && (
              <Tabs value={active.status} onValueChange={(v) => setTab(v as ContentStatus)}>
                <TabsList aria-label="Статус постов">
                  {columns.map((c) => (
                    <TabsTrigger key={c.status} value={c.status}>
                      {c.label}
                      <span className="rounded bg-muted px-1 text-xs text-muted-foreground tabular">{c.posts.length}</span>
                    </TabsTrigger>
                  ))}
                </TabsList>
                {columns.map((c) => (
                  <TabsContent key={c.status} value={c.status}>
                    {c.posts.length === 0 ? (
                      <p className="py-8 text-center text-sm text-muted-foreground">В этом статусе постов нет</p>
                    ) : (
                      <div className="grid gap-3 sm:grid-cols-2">
                        {c.posts.map((item) => {
                          const Icon = KIND_ICONS[item.post.kind] ?? Camera;
                          return (
                            <button
                              key={item.post.id}
                              type="button"
                              onClick={() => open(item.post)}
                              aria-current={current?.id === item.post.id ? 'true' : undefined}
                              className={cn(
                                'flex flex-col gap-2 rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent/50',
                                current?.id === item.post.id && 'border-primary ring-1 ring-primary',
                              )}
                            >
                              <span className="flex items-center gap-2 text-sm">
                                <Icon className="size-4 text-muted-foreground" aria-label={item.kind} />
                                <span className="flex-1 truncate font-medium">{item.persona || 'Без персонажа'}</span>
                                <span className="text-xs text-muted-foreground">{item.kind}</span>
                              </span>
                              <span className="line-clamp-3 text-sm [overflow-wrap:anywhere]">{item.post.caption || 'Без подписи'}</span>
                              {item.when && <span className="text-xs text-muted-foreground">{item.when}</span>}
                              {item.error && <span className="text-xs text-destructive">{item.error}</span>}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </TabsContent>
                ))}
              </Tabs>
            )
          )}
        </div>

        {!isPhone && selected !== null && (
          <aside className="rounded-xl border bg-card p-4 lg:sticky lg:top-4 lg:max-h-[calc(100dvh-2rem)] lg:overflow-y-auto">{editor}</aside>
        )}
      </div>

      {isPhone && (
        <Sheet open={selected !== null} onOpenChange={(o) => !o && setSelected(null)}>
          <SheetContent side="bottom" className="max-h-[92dvh] overflow-y-auto px-4 pt-4 [&>button:last-child]:hidden">
            <SheetTitle className="sr-only">{current ? 'Редактирование поста' : 'Новый пост'}</SheetTitle>
            <SheetDescription className="sr-only">Редактор поста</SheetDescription>
            {editor}
          </SheetContent>
        </Sheet>
      )}
    </Page>
  );
}
