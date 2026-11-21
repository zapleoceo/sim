import { FileText, Play } from 'lucide-react';
import { useRef, useState } from 'react';
import { formatDuration } from '@/lib/format';
import type { Media, MessageKind } from '@/lib/types';

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

const RING_R = 47;
const RING_LENGTH = 2 * Math.PI * RING_R;

/** The progress of a video note as the length of its ring stroke (0..1 of the circle). */
export function ringOffset(current: number, duration: number): number {
  const part = duration > 0 ? Math.min(Math.max(current / duration, 0), 1) : 0;
  return RING_LENGTH * (1 - part);
}

/**
 * A video note («кружок») as Telegram shows it: a circle with no player controls or seek bar.
 * A click plays it with sound, another click pauses; a thin ring along the edge shows progress.
 */
export function RoundVideo({ src, label, durationS }: { src: string; label: string; durationS: number | null }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [offset, setOffset] = useState(RING_LENGTH);
  const toggle = () => {
    const video = ref.current;
    if (!video) return;
    if (video.paused) void video.play().catch(() => setPlaying(false));
    else video.pause();
  };
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`${label}: ${playing ? 'пауза' : 'воспроизвести'}`}
      className="relative size-56 flex-none overflow-hidden rounded-full bg-black focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <video
        ref={ref}
        src={src}
        preload="metadata"
        playsInline
        className="size-full rounded-full object-cover"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => { setPlaying(false); setOffset(RING_LENGTH); }}
        onTimeUpdate={(e) => setOffset(ringOffset(e.currentTarget.currentTime, e.currentTarget.duration))}
      />
      <svg viewBox="0 0 100 100" className="pointer-events-none absolute inset-0 size-full -rotate-90" aria-hidden="true">
        <circle cx="50" cy="50" r={RING_R} fill="none" stroke="white" strokeOpacity="0.85" strokeWidth="2"
          strokeDasharray={RING_LENGTH} strokeDashoffset={offset} />
      </svg>
      {!playing && (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-black/45 text-white">
            <Play className="size-6" fill="currentColor" />
          </span>
        </span>
      )}
      {durationS ? (
        <span className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-black/45 px-2 text-xs text-white tabular">
          {formatDuration(durationS)}
        </span>
      ) : null}
    </button>
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
  if (media.kind === 'video' && messageKind === 'video_note') {
    return (
      <figure className="flex flex-col gap-1.5">
        <RoundVideo src={media.url} label="Кружок" durationS={media.duration_s} />
        <Understood label="В кружке" text={media.description} />
      </figure>
    );
  }
  if (media.kind === 'video') {
    return (
      <figure className="flex flex-col gap-1.5">
        <video
          controls
          preload="metadata"
          src={media.url}
          aria-label={media.caption || 'Видео'}
          className="max-h-80 max-w-full rounded-lg bg-black"
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
