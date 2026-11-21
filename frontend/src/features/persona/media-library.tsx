import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ImagePlus, Images, Pencil, Star, Trash2, Upload, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Section } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { tabTriggerClass, tabsListClass } from '@/components/ui/tabs';
import { qk } from '@/lib/query';
import type { Media } from '@/lib/types';
import { cn, splitTags } from '@/lib/utils';
import { mediaApi, type MediaKindFilter, type MediaPatch, type MediaSourceFilter } from './api';

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

function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className={tabsListClass}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          data-state={o.value === value ? 'active' : 'inactive'}
          className={tabTriggerClass}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function useMediaInvalidate(): () => void {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: qk.media });
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
    mutationFn: () => mediaApi.upload({ file: file as File, caption: caption.trim(), tags: splitTags(tags), isReference }),
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

function MediaCard({ media }: { media: Media }) {
  const invalidate = useMediaInvalidate();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const [caption, setCaption] = useState(media.caption);
  const [tags, setTags] = useState(media.tags.join(', '));

  const update = useMutation({
    mutationFn: (patch: MediaPatch) => mediaApi.update(media.id, patch),
    onSuccess: (_m, patch) => {
      toast.success('is_reference' in patch && !('caption' in patch) ? 'Отметка референса изменена' : 'Подпись и теги сохранены');
      setEditing(false);
      invalidate();
    },
  });
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
  const reference = update.isPending && update.variables && 'is_reference' in update.variables
    ? Boolean(update.variables.is_reference)
    : media.is_reference;
  const alt = media.caption || media.description || 'Фото';

  return (
    <li className="flex min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-xs">
      <div className="relative aspect-square bg-muted">
        {media.kind === 'video' ? (
          <video src={media.url} preload="metadata" controls muted playsInline className="size-full object-cover" aria-label={media.caption || 'Видео'} />
        ) : (
          <a href={media.url} target="_blank" rel="noreferrer">
            <img src={media.url} alt={alt} loading="lazy" className="size-full object-cover" />
          </a>
        )}
        <div className="pointer-events-none absolute top-1.5 left-1.5 flex flex-wrap gap-1">
          {media.source === 'generated' && <Badge variant="secondary">сгенерировано</Badge>}
          {reference && (
            <Badge variant="warning" className="bg-background/90">
              <Star />
              референс
            </Badge>
          )}
        </div>
      </div>
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
          <div className="mt-auto flex items-center justify-between gap-1 pt-1">
            {media.kind === 'photo' ? (
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Switch
                  checked={reference}
                  onCheckedChange={(v) => update.mutate({ is_reference: v })}
                  disabled={update.isPending}
                  aria-label="Референс внешности"
                />
                референс
              </label>
            ) : <span />}
            <span className="flex gap-0.5">
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

/** Library and generated media of the persona: filter, upload, edit, delete. */
export function MediaLibrarySection() {
  const [source, setSource] = useState<MediaSourceFilter>('all');
  const [kind, setKind] = useState<MediaKindFilter>('all');
  const media = useQuery({ queryKey: qk.mediaList(source, kind), queryFn: () => mediaApi.list(source, kind) });
  // «Все» = медиатека + сгенерированные; присланное клиентами живёт в чатах.
  const items = (media.data?.items ?? []).filter((m) => m.source !== 'incoming' && (m.kind === 'photo' || m.kind === 'video'));

  return (
    <Section
      id="media"
      title="Медиатека"
      description="Фото и видео, которые персонаж может отправить. Референсы внешности уходят в генерацию как образец."
    >
      <MediaUploader />
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Источник" value={source} options={SOURCES} onChange={setSource} />
        <Segmented label="Тип" value={kind} options={KINDS} onChange={setKind} />
      </div>
      {media.isPending && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
          {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="aspect-square w-full rounded-xl" />)}
        </div>
      )}
      {media.isError && <ErrorState onRetry={() => void media.refetch()} />}
      {media.isSuccess && items.length === 0 && (
        <EmptyState icon={Images} title="Здесь пока пусто" hint="Загрузите фото или видео выше." />
      )}
      {items.length > 0 && (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
          {items.map((m) => <MediaCard key={m.id} media={m} />)}
        </ul>
      )}
    </Section>
  );
}
