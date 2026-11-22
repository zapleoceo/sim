import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { House, ImagePlus, Images, Pencil, Play, Star, Trash2, Upload, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { MutationSwitch } from '@/components/mutation-switch';
import { Section } from '@/components/field';
import { Segmented } from '@/components/segmented';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { formatDuration } from '@/lib/format';
import { qk } from '@/lib/query';
import type { Media } from '@/lib/types';
import { cn, splitTags } from '@/lib/utils';
import { mediaApi, type MediaKindFilter, type MediaPatch, type MediaSourceFilter } from './api';
import { MediaLightbox } from './media-lightbox';
import { usePersonaId } from './persona-scope';

const SOURCES: { value: MediaSourceFilter; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'library', label: 'Медиатека' },
  { value: 'generated', label: 'Сгенерированные' },
];

const KINDS: { value: MediaKindFilter; label: string }[] = [
  { value: 'all', label: 'Фото и видео' },
  { value: 'photo', label: 'Фото' },
  { value: 'video', label: 'Видео' },
];

function useMediaInvalidate(): () => void {
  const queryClient = useQueryClient();
  const personaId = usePersonaId();
  return () => void queryClient.invalidateQueries({ queryKey: qk.media(personaId) });
}

function useObjectUrl(file: File | null): string | null {
  const url = useMemo(
    () => (file && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : null),
    [file],
  );
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  return url;
}

/** Drag-drop or pick one photo/video, give it a caption and tags, mark it as an appearance reference. */
function MediaUploader() {
  const personaId = usePersonaId();
  const invalidate = useMediaInvalidate();
  const picker = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [caption, setCaption] = useState('');
  const [tags, setTags] = useState('');
  const [isReference, setIsReference] = useState(false);
  const [dragging, setDragging] = useState(false);
  const preview = useObjectUrl(file);

  const clear = (): void => {
    setFile(null);
    setCaption('');
    setTags('');
    setIsReference(false);
  };
  const upload = useMutation({
    mutationFn: () =>
      mediaApi.upload(personaId, { file: file as File, caption: caption.trim(), tags: splitTags(tags), isReference }),
    onSuccess: () => {
      toast.success('Файл добавлен в медиатеку');
      clear();
      invalidate();
    },
  });

  const pick = (f: File | undefined | null): void => {
    if (!f) return;
    if (!f.type.startsWith('image/') && !f.type.startsWith('video/')) {
      toast.error('Можно загрузить только фото или видео');
      return;
    }
    setFile(f);
    if (!f.type.startsWith('image/')) setIsReference(false);
  };
  const onDrop = (e: DragEvent<HTMLElement>): void => {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files?.[0]);
  };

  const fileInput = (
    <input
      ref={picker}
      type="file"
      accept="image/*,video/*"
      className="sr-only"
      tabIndex={-1}
      aria-hidden="true"
      onChange={(e) => {
        pick(e.target.files?.[0]);
        e.target.value = '';
      }}
    />
  );

  if (!file) {
    return (
      <div>
        {fileInput}
        <button
          type="button"
          onClick={() => picker.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            'flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:bg-accent/40 focus-visible:ring-[3px] focus-visible:ring-ring focus-visible:outline-none',
            dragging && 'border-primary bg-accent/60',
          )}
        >
          <ImagePlus className="size-6" />
          <span>
            <span className="font-medium text-foreground">Перетащите фото или видео</span> или нажмите, чтобы выбрать
          </span>
        </button>
      </div>
    );
  }

  const isPhoto = file.type.startsWith('image/');
  return (
    <form
      className="flex flex-col gap-4 rounded-xl border p-3 sm:flex-row"
      onSubmit={(e) => {
        e.preventDefault();
        upload.mutate();
      }}
    >
      <div className="flex size-32 flex-none items-center justify-center overflow-hidden rounded-lg bg-muted">
        {preview && isPhoto && <img src={preview} alt="Предпросмотр" className="size-full object-cover" />}
        {preview && !isPhoto && <video src={preview} muted playsInline className="size-full object-cover" />}
        {!preview && <span className="px-2 text-center text-xs text-muted-foreground">{file.name}</span>}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <p className="truncate text-sm font-medium">{file.name}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="upload-caption">Подпись</Label>
            <Input id="upload-caption" value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="Что на фото, одной строкой" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="upload-tags">Теги через запятую</Label>
            <Input id="upload-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="селфи, утро, дом" />
          </div>
        </div>
        {isPhoto && (
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={isReference} onCheckedChange={setIsReference} />
            Референс внешности (уходит в генерацию как образец лица и фигуры)
          </label>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={clear} disabled={upload.isPending}>
            <X />
            Отмена
          </Button>
          <Button type="submit" disabled={upload.isPending}>
            <Upload />
            {upload.isPending ? 'Загружаю…' : 'Загрузить'}
          </Button>
        </div>
      </div>
    </form>
  );
}

const PATCH_TOASTS: [keyof MediaPatch, (patch: MediaPatch) => string][] = [
  ['is_room_ref', (p) => (p.is_room_ref ? 'Фото стало эталоном комнаты' : 'Фото больше не эталон комнаты')],
  ['is_reference', (p) => (p.is_reference ? 'Фото стало референсом внешности' : 'Отметка референса снята')],
];

/** One media file's PATCH with its toast; the library refreshes after it. */
function useMediaPatch(media: Media, onSaved?: () => void) {
  const invalidate = useMediaInvalidate();
  return useMutation({
    mutationFn: (patch: MediaPatch) => mediaApi.update(media.id, patch),
    onSuccess: (_m, patch) => {
      const flag = PATCH_TOASTS.find(([key]) => key in patch && !('caption' in patch));
      toast.success(flag ? flag[1](patch) : 'Подпись и теги сохранены');
      onSaved?.();
      invalidate();
    },
  });
}

/**
 * «Референс лица» (any photo: her face for every generation) and «эталон комнаты» (a generated
 * photo of a room of her apartment: sent first among the room's references).
 */
export function ReferenceSwitches({ media, className }: { media: Media; className?: string }) {
  const update = useMediaPatch(media);
  if (media.kind !== 'photo') return null;
  const saving = (key: 'is_reference' | 'is_room_ref'): boolean | undefined =>
    update.variables && key in update.variables ? Boolean(update.variables[key]) : undefined;
  const room = media.source === 'generated' ? media.scene?.room : undefined;
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <MutationSwitch
        checked={media.is_reference}
        pending={update.isPending}
        pendingValue={saving('is_reference')}
        onCheckedChange={(v) => update.mutate({ is_reference: v })}
        label="референс лица"
        title="Уходит в каждую генерацию как образец лица, причёски и фигуры"
        aria-label="Референс внешности"
        className="gap-1.5 text-xs text-muted-foreground"
      />
      {room && (
        <MutationSwitch
          checked={Boolean(media.scene?.room_ref)}
          pending={update.isPending}
          pendingValue={saving('is_room_ref')}
          onCheckedChange={(v) => update.mutate({ is_room_ref: v })}
          label={`эталон комнаты «${room}»`}
          title="Уходит первым образцом во все фото этой комнаты: та же мебель, лампы и вид из окна"
          aria-label={`Эталон комнаты «${room}»`}
          className="gap-1.5 text-xs text-muted-foreground"
        />
      )}
    </div>
  );
}

function MediaTile({ media, onOpen }: { media: Media; onOpen: () => void }) {
  const what = media.kind === 'video' ? 'Видео' : 'Фото';
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group relative block aspect-square w-full overflow-hidden bg-muted focus-visible:ring-[3px] focus-visible:ring-ring focus-visible:outline-none"
      aria-label={`Открыть: ${media.caption || what}`}
    >
      {media.kind === 'video' ? (
        <>
          <video src={media.url} preload="metadata" muted playsInline className="size-full object-cover" aria-hidden="true" />
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-black/55 text-white transition-transform group-hover:scale-110">
              <Play className="size-5 translate-x-px fill-current" />
            </span>
          </span>
          {media.duration_s != null && (
            <span className="pointer-events-none absolute right-1.5 bottom-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[11px] text-white tabular-nums">
              {formatDuration(media.duration_s)}
            </span>
          )}
        </>
      ) : (
        <img
          src={media.url}
          alt={media.caption || media.description || what}
          loading="lazy"
          className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
        />
      )}
      <span className="pointer-events-none absolute top-1.5 left-1.5 flex flex-wrap gap-1">
        {media.source === 'generated' && <Badge variant="secondary">сгенерировано</Badge>}
        {media.is_reference && (
          <Badge variant="warning" className="bg-background/90">
            <Star />
            лицо
          </Badge>
        )}
        {media.scene?.room_ref && (
          <Badge variant="warning" className="bg-background/90">
            <House />
            {media.scene.room}
          </Badge>
        )}
      </span>
    </button>
  );
}

function MediaCard({ media, onOpen }: { media: Media; onOpen: () => void }) {
  const invalidate = useMediaInvalidate();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const [caption, setCaption] = useState(media.caption);
  const [tags, setTags] = useState(media.tags.join(', '));
  const update = useMediaPatch(media, () => setEditing(false));
  const remove = useMutation({
    mutationFn: () => mediaApi.remove(media.id),
    onSuccess: () => {
      toast.success('Файл удалён');
      invalidate();
    },
  });

  const startEdit = (): void => {
    setCaption(media.caption);
    setTags(media.tags.join(', '));
    setEditing(true);
  };
  const askRemove = async (): Promise<void> => {
    const ok = await confirm({
      title: 'Удалить файл?',
      message: `«${media.caption || media.file_name || 'Без подписи'}» пропадёт из медиатеки. Уже отправленные сообщения останутся.`,
      confirmLabel: 'Удалить',
      danger: true,
    });
    if (ok) remove.mutate();
  };

  return (
    <li className="flex min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-xs">
      <MediaTile media={media} onOpen={onOpen} />
      {editing ? (
        <form
          className="flex flex-col gap-2 p-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            update.mutate({ caption: caption.trim(), tags: splitTags(tags) });
          }}
        >
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">Подпись</span>
            <Input value={caption} onChange={(e) => setCaption(e.target.value)} className="h-8" autoFocus />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">Теги через запятую</span>
            <Input value={tags} onChange={(e) => setTags(e.target.value)} className="h-8" />
          </label>
          <div className="flex justify-end gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Отмена</Button>
            <Button type="submit" size="sm" disabled={update.isPending}>Сохранить</Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-1 flex-col gap-1.5 p-2.5">
          <p className={cn('line-clamp-2 text-sm', !media.caption && 'text-muted-foreground italic')}>
            {media.caption || 'Без подписи'}
          </p>
          {media.tags.length > 0 && (
            <ul className="flex flex-wrap gap-1" aria-label="Теги">
              {media.tags.map((t) => (
                <li key={t} className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">#{t}</li>
              ))}
            </ul>
          )}
          <div className="mt-auto flex items-end justify-between gap-1 pt-1">
            {media.kind === 'photo' ? <ReferenceSwitches media={media} className="min-w-0" /> : <span />}
            <span className="flex flex-none gap-0.5">
              <Button variant="ghost" size="icon-sm" onClick={startEdit} aria-label="Изменить подпись и теги">
                <Pencil />
              </Button>
              <Button variant="ghost-destructive" size="icon-sm" onClick={() => void askRemove()} disabled={remove.isPending} aria-label="Удалить">
                <Trash2 />
              </Button>
            </span>
          </div>
        </div>
      )}
    </li>
  );
}

const GRID = 'grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6';

/** Library and generated media of the persona: filter, upload, view full screen, edit, delete. */
export function MediaLibrarySection() {
  const personaId = usePersonaId();
  const [source, setSource] = useState<MediaSourceFilter>('all');
  const [kind, setKind] = useState<MediaKindFilter>('all');
  const [open, setOpen] = useState<number | null>(null);
  const media = useQuery({
    queryKey: qk.mediaList(personaId, source, kind),
    queryFn: () => mediaApi.list(personaId, source, kind),
  });
  // «Все» = медиатека + сгенерированные; присланное клиентами живёт в чатах.
  const items = (media.data?.items ?? []).filter((m) => m.source !== 'incoming' && (m.kind === 'photo' || m.kind === 'video'));
  const filter = <T,>(set: (value: T) => void) => (value: T): void => {
    set(value);
    setOpen(null);
  };

  return (
    <Section
      id="media"
      title="Медиатека"
      description="Фото и видео, которые персонаж может отправить. Нажмите на файл — он откроется на весь экран, листайте стрелками или свайпом. Референсы лица и эталоны комнат уходят в генерацию как образец."
    >
      <MediaUploader />
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Источник" value={source} options={SOURCES} onChange={filter(setSource)} />
        <Segmented label="Тип" value={kind} options={KINDS} onChange={filter(setKind)} />
        {items.length > 0 && <span className="ml-auto text-sm text-muted-foreground">Файлов: {items.length}</span>}
      </div>
      {media.isPending && (
        <div className={GRID}>
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="aspect-square w-full rounded-xl" />)}
        </div>
      )}
      {media.isError && <ErrorState onRetry={() => void media.refetch()} />}
      {media.isSuccess && items.length === 0 && (
        <EmptyState icon={Images} title="Здесь пока пусто" hint="Загрузите фото или видео выше." />
      )}
      {items.length > 0 && (
        <ul className={GRID}>
          {items.map((m, i) => <MediaCard key={m.id} media={m} onOpen={() => setOpen(i)} />)}
        </ul>
      )}
      <MediaLightbox
        items={items}
        index={open}
        onIndexChange={setOpen}
        onClose={() => setOpen(null)}
        actions={(m) => <ReferenceSwitches media={m} className="border-t pt-3" />}
      />
    </Section>
  );
}
