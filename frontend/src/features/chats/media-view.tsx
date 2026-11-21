import { FileText } from 'lucide-react';
import { formatDuration } from '@/lib/format';
import type { Media, MessageKind } from '@/lib/types';
import { cn } from '@/lib/utils';

/** What a model understood from the attachment, under the media itself. */
function Understood({ label, text }: { label: string; text: string }) {
  if (!text.trim()) return null;
  return (
    <p className="text-xs leading-snug text-muted-foreground">
      <span className="font-medium">{label}: </span>
      <span className="whitespace-pre-wrap">{text}</span>
    </p>
  );
}

/**
 * An attachment inside a bubble: photo, video (`<video controls>`), voice (`<audio controls>`)
 * with its transcript, or a file chip with its summary.
 */
export function MediaView({ media, messageKind }: { media: Media; messageKind?: MessageKind }) {
  const alt = media.caption || media.description || 'Фото';
  if (media.kind === 'photo') {
    return (
      <figure className="flex flex-col gap-1.5">
        <a href={media.url} target="_blank" rel="noreferrer" className="block w-fit">
          <img
            src={media.url}
            alt={alt}
            loading="lazy"
            className="max-h-80 w-auto max-w-full rounded-lg object-contain"
            width={media.width ?? undefined}
            height={media.height ?? undefined}
          />
        </a>
        <Understood label="На фото" text={media.description} />
      </figure>
    );
  }
  if (media.kind === 'video') {
    const round = messageKind === 'video_note';
    return (
      <figure className="flex flex-col gap-1.5">
        <video
          controls
          preload="metadata"
          src={media.url}
          aria-label={media.caption || 'Видео'}
          className={cn('max-h-80 max-w-full rounded-lg bg-black', round && 'size-56 rounded-full object-cover')}
        />
        <Understood label="На видео" text={media.description} />
      </figure>
    );
  }
  if (media.kind === 'audio') {
    return (
      <div className="flex flex-col gap-1.5">
        <audio controls preload="metadata" src={media.url} aria-label="Голосовое" className="h-10 w-64 max-w-full" />
        {media.duration_s ? <span className="text-xs text-muted-foreground tabular">{formatDuration(media.duration_s)}</span> : null}
        <Understood label="Расшифровка" text={media.description} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <a
        href={media.url}
        target="_blank"
        rel="noreferrer"
        download={media.file_name || undefined}
        className="flex w-fit max-w-full items-center gap-2 rounded-lg border bg-background/60 px-3 py-2 text-sm hover:bg-accent"
      >
        <FileText className="size-4 flex-none text-muted-foreground" />
        <span className="truncate">{media.file_name || 'Файл'}</span>
      </a>
      <Understood label="Содержимое" text={media.description} />
    </div>
  );
}
