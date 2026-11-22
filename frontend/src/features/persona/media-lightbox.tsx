import { ChevronLeft, ChevronRight, ExternalLink, XIcon } from 'lucide-react';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { useEffect, useRef, type KeyboardEvent, type ReactNode, type TouchEvent } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { formatDateTime } from '@/lib/format';
import type { Media } from '@/lib/types';
import { cn } from '@/lib/utils';
import { lightboxTitle, originLabel, sceneRows, stepIndex, swipeStep } from './media-lightbox-logic';

/** Warms the browser cache for the photos next to the open one, so paging is instant. */
function usePreload(urls: string[]): void {
  const key = urls.join('|');
  useEffect(() => {
    if (typeof Image === 'undefined') return;
    for (const url of key.split('|').filter(Boolean)) {
      const img = new Image();
      img.src = url;
    }
  }, [key]);
}

function MediaStage({ media }: { media: Media }) {
  const alt = media.caption || media.description || 'Фото';
  return media.kind === 'video' ? (
    <video
      key={media.id}
      src={media.url}
      controls
      autoPlay
      playsInline
      className="max-h-full max-w-full rounded-md bg-black object-contain"
      aria-label={media.caption || 'Видео'}
    />
  ) : (
    <img key={media.id} src={media.url} alt={alt} className="max-h-full max-w-full rounded-md object-contain select-none" draggable={false} />
  );
}

function Details({ media, children }: { media: Media; children?: ReactNode }) {
  const scene = sceneRows(media.scene);
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex flex-col gap-1.5">
        <p className={cn('text-base leading-snug', !media.caption && 'text-muted-foreground italic')}>{media.caption || 'Без подписи'}</p>
        {media.tags.length > 0 && (
          <ul className="flex flex-wrap gap-1" aria-label="Теги">
            {media.tags.map((t) => (
              <li key={t} className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">#{t}</li>
            ))}
          </ul>
        )}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
        <dt className="text-muted-foreground">Откуда</dt>
        <dd>{originLabel(media)}</dd>
        {media.created_at && (
          <>
            <dt className="text-muted-foreground">Дата</dt>
            <dd>{formatDateTime(media.created_at)}</dd>
          </>
        )}
      </dl>
      {scene.length > 0 && (
        <section aria-label="Сцена генерации" className="flex flex-col gap-1.5">
          <h3 className="flex items-center gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Сцена генерации
            {media.scene?.room_ref && <Badge variant="warning">эталон комнаты</Badge>}
          </h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
            {scene.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="break-words">{value}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {media.description && media.description !== media.caption && (
        <p className="text-xs text-muted-foreground">{media.description}</p>
      )}
      {children}
    </div>
  );
}

/**
 * Full-screen viewer of a gallery: arrows, ← → keys and a swipe page through `items`, Esc
 * closes; a video plays inside. Next to the picture: caption, tags, date, whose file it is and
 * the scene it was generated in; `actions` adds controls for the open item (docs/frontend.md).
 */
export function MediaLightbox({ items, index, onIndexChange, onClose, actions }: {
  items: Media[];
  /** The open item; null = closed. */
  index: number | null;
  onIndexChange: (index: number) => void;
  onClose: () => void;
  actions?: (media: Media) => ReactNode;
}) {
  const open = index !== null && items.length > 0;
  const current = index === null ? 0 : Math.max(0, Math.min(index, items.length - 1));
  const media = items[current];
  const touch = useRef<{ x: number; y: number } | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const many = items.length > 1;
  const neighbours = many
    ? [items[stepIndex(current, 1, items.length)], items[stepIndex(current, -1, items.length)]]
        .filter((m): m is Media => m !== undefined && m.kind === 'photo')
        .map((m) => m.url)
    : [];
  usePreload(open ? neighbours : []);

  const go = (delta: number): void => {
    if (many) onIndexChange(stepIndex(current, delta, items.length));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    // A focused video seeks with the arrows; everywhere else they turn the page.
    if (e.target instanceof HTMLVideoElement || e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      go(1);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      go(-1);
    }
  };
  const onTouchStart = (e: TouchEvent<HTMLDivElement>): void => {
    const t = e.touches[0];
    touch.current = t ? { x: t.clientX, y: t.clientY } : null;
  };
  const onTouchEnd = (e: TouchEvent<HTMLDivElement>): void => {
    const start = touch.current;
    const t = e.changedTouches[0];
    touch.current = null;
    if (!start || !t) return;
    const step = swipeStep(t.clientX - start.x, t.clientY - start.y);
    if (step) go(step);
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/90 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          ref={content}
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            content.current?.focus();
          }}
          onKeyDown={onKeyDown}
          className="fixed inset-0 z-50 flex flex-col outline-none lg:flex-row"
        >
          {media && (
            <>
              <DialogPrimitive.Title className="sr-only">{lightboxTitle(media, current, items.length)}</DialogPrimitive.Title>
              <div
                className="relative flex min-h-0 flex-1 items-center justify-center p-2 pt-12 sm:p-4 sm:pt-14 lg:p-6"
                onTouchStart={onTouchStart}
                onTouchEnd={onTouchEnd}
                data-testid="lightbox-stage"
              >
                <MediaStage media={media} />
                {many && (
                  <>
                    <Button
                      variant="secondary"
                      size="icon"
                      className="absolute top-1/2 left-2 -translate-y-1/2 rounded-full bg-background/70 backdrop-blur sm:left-4"
                      onClick={() => go(-1)}
                      aria-label="Предыдущее"
                    >
                      <ChevronLeft />
                    </Button>
                    <Button
                      variant="secondary"
                      size="icon"
                      className="absolute top-1/2 right-2 -translate-y-1/2 rounded-full bg-background/70 backdrop-blur sm:right-4"
                      onClick={() => go(1)}
                      aria-label="Следующее"
                    >
                      <ChevronRight />
                    </Button>
                  </>
                )}
                <div className="absolute top-2 right-2 left-2 flex items-center justify-between gap-2 text-sm text-white/85 sm:top-3 sm:right-4 sm:left-4">
                  <span aria-live="polite">{many ? `${current + 1} / ${items.length}` : ''}</span>
                  <span className="flex gap-1">
                    <Button asChild variant="ghost" size="icon" className="text-white hover:bg-white/15 hover:text-white">
                      <a href={media.url} target="_blank" rel="noreferrer" aria-label="Открыть файл в новой вкладке">
                        <ExternalLink />
                      </a>
                    </Button>
                    <DialogPrimitive.Close asChild>
                      <Button variant="ghost" size="icon" className="text-white hover:bg-white/15 hover:text-white" aria-label="Закрыть">
                        <XIcon />
                      </Button>
                    </DialogPrimitive.Close>
                  </span>
                </div>
              </div>
              <aside
                aria-label="О файле"
                className="max-h-[40dvh] shrink-0 overflow-y-auto border-t bg-background p-4 lg:max-h-none lg:w-96 lg:border-t-0 lg:border-l lg:p-5"
              >
                <Details media={media}>{actions?.(media)}</Details>
              </aside>
            </>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
