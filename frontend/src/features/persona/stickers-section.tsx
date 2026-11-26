import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, Sticker as StickerIcon } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Section } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { qk } from '@/lib/query';
import { stickerApi } from './api';
import { usePersonaId } from './persona-scope';
import { groupStickers, stickerEmoji } from './sticker-logic';

/** The server re-reads the stickers in the background; the list is read again after this delay. */
export const STICKER_REFETCH_MS = 5_000;

const EMPTY_HINT =
  'У её аккаунта Telegram нет стикеров. Установите наборы или добавьте стикеры в избранное в её Telegram — ' +
  'Марина будет отправлять их сама, изредка и к месту. Потом нажмите «Обновить».';

/**
 * «Стикеры»: the stickers of her own Telegram account (installed sets and favourites), grouped by
 * set, and «Обновить» — re-read them from Telegram. Only shows them: she picks a sticker herself.
 */
export function StickersSection({ refetchAfterMs = STICKER_REFETCH_MS }: { refetchAfterMs?: number }) {
  const personaId = usePersonaId();
  const queryClient = useQueryClient();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const stickers = useQuery({ queryKey: qk.stickers(personaId), queryFn: () => stickerApi.list(personaId) });
  const invalidate = (): Promise<void> => queryClient.invalidateQueries({ queryKey: qk.stickers(personaId) });
  const sync = useMutation({
    mutationFn: () => stickerApi.sync(personaId),
    onSuccess: () => {
      toast.info('Обновляю стикеры из Telegram…');
      void invalidate();
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void invalidate(), refetchAfterMs);
    },
  });

  const items = stickers.data?.items ?? [];
  const groups = groupStickers(items);

  return (
    <Section
      id="stickers"
      title="Стикеры"
      description="Стикеры её аккаунта Telegram: установленные наборы и избранное. Марина отправляет их сама — изредка и к месту."
      actions={
        <Button variant="outline" size="sm" disabled={sync.isPending} onClick={() => sync.mutate()}>
          <RefreshCw className={sync.isPending ? 'animate-spin' : undefined} />
          Обновить
        </Button>
      }
    >
      {stickers.isPending && <Skeleton className="h-24 w-full rounded-xl" />}
      {stickers.isError && <ErrorState title="Стикеры не загрузились" onRetry={() => void stickers.refetch()} />}
      {stickers.isSuccess && items.length === 0 && (
        <EmptyState icon={StickerIcon} title="Стикеров пока нет" hint={EMPTY_HINT} />
      )}
      {items.length > 0 && (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">Стикеров: {items.length}</p>
          {groups.map((g) => (
            <section key={g.key} aria-label={g.title} className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">
                {g.title} <span className="font-normal text-muted-foreground">· {g.stickers.length + g.hidden}</span>
              </h3>
              <ul className="flex flex-wrap gap-1.5">
                {g.stickers.map((s) => (
                  <li key={s.id}>
                    <Badge variant="muted" className="px-1.5 text-base leading-6" title={s.set_title || s.set_name}>
                      {stickerEmoji(s)}
                    </Badge>
                  </li>
                ))}
                {g.hidden > 0 && (
                  <li>
                    <Badge variant="outline" className="leading-6">ещё {g.hidden}</Badge>
                  </li>
                )}
              </ul>
            </section>
          ))}
        </div>
      )}
    </Section>
  );
}
