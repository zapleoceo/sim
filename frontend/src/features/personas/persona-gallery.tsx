import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Camera, Circle, Film, ImagePlus, Trash2, Upload } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { personasApi } from '@/lib/api/personas';
import type { MediaKind, PersonaMedia } from '@/lib/models';
import { qk } from '@/lib/query';

const KIND_LABELS: Record<MediaKind, string> = { photo: 'Фото', video: 'Видео', video_note: 'Кружочки' };
const KIND_ICONS: Record<MediaKind, typeof Camera> = { photo: Camera, video: Film, video_note: Circle };
const ACCEPT: Record<MediaKind, string> = { photo: 'image/jpeg,image/png,image/webp,image/gif', video: 'video/mp4,video/webm', video_note: 'video/mp4' };
const ALL_KINDS: MediaKind[] = ['photo', 'video', 'video_note'];

export function PersonaGallery({ personaId }: { personaId: number }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [filter, setFilter] = useState<MediaKind | null>(null);
  const [uploadKind, setUploadKind] = useState<MediaKind>('photo');
  const fileRef = useRef<HTMLInputElement>(null);

  const media = useQuery({
    queryKey: qk.personaMedia(personaId),
    queryFn: () => personasApi.media.list(personaId),
  });

  const upload = useMutation({
    mutationFn: (file: File) => personasApi.media.upload(personaId, file, uploadKind),
    onSuccess: (item: PersonaMedia) => {
      queryClient.setQueryData<PersonaMedia[]>(qk.personaMedia(personaId), (list) => [item, ...(list ?? [])]);
      toast.success('Файл загружен');
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => personasApi.media.remove(personaId, id),
    onSuccess: (_, id) => {
      queryClient.setQueryData<PersonaMedia[]>(qk.personaMedia(personaId), (list) => (list ?? []).filter((m) => m.id !== id));
    },
  });

  const askRemove = useCallback(async (m: PersonaMedia) => {
    const ok = await confirm({
      title: 'Удалить файл?',
      message: `${KIND_LABELS[m.kind]}${m.caption ? ` «${m.caption}»` : ''} будет удалён.`,
      confirmLabel: 'Удалить',
      danger: true,
    });
    if (ok) remove.mutate(m.id);
  }, [confirm, remove]);

  const onFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) upload.mutate(file);
    e.target.value = '';
  }, [upload]);

  const openPicker = useCallback((kind: MediaKind) => {
    setUploadKind(kind);
    setTimeout(() => fileRef.current?.click(), 0);
  }, []);

  const filtered = filter ? (media.data ?? []).filter((m) => m.kind === filter) : (media.data ?? []);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Галерея</h3>
        <div className="flex gap-1">
          {ALL_KINDS.map((k) => {
            const Icon = KIND_ICONS[k];
            return (
              <Button
                key={k}
                size="sm"
                variant="ghost"
                title={`Загрузить ${KIND_LABELS[k].toLowerCase()}`}
                onClick={() => openPicker(k)}
                disabled={upload.isPending}
              >
                <Icon className="mr-1 size-4" />
                <Upload className="size-3" />
              </Button>
            );
          })}
        </div>
      </div>

      <input ref={fileRef} type="file" className="hidden" accept={ACCEPT[uploadKind]} onChange={onFileChange} />

      {/* Filters */}
      <div className="flex flex-wrap gap-1">
        <Button size="sm" variant={filter === null ? 'default' : 'outline'} onClick={() => setFilter(null)}>
          Все
        </Button>
        {ALL_KINDS.map((k) => (
          <Button key={k} size="sm" variant={filter === k ? 'default' : 'outline'} onClick={() => setFilter(k)}>
            {KIND_LABELS[k]}
          </Button>
        ))}
      </div>

      {media.isSuccess && filtered.length === 0 && (
        <EmptyState icon={ImagePlus} title="Пока пусто" hint="Загрузите фото, видео или кружочки" />
      )}

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {filtered.map((m) => (
          <MediaCard key={m.id} media={m} onRemove={() => void askRemove(m)} />
        ))}
      </div>
    </div>
  );
}

function MediaCard({ media: m, onRemove }: { media: PersonaMedia; onRemove: () => void }) {
  const isImage = m.kind === 'photo';
  const isVideo = m.kind === 'video' || m.kind === 'video_note';
  const Icon = KIND_ICONS[m.kind];

  return (
    <div className="group relative aspect-square overflow-hidden rounded-lg border bg-muted">
      {isImage && (
        <img
          src={m.file_url}
          alt={m.caption || KIND_LABELS[m.kind]}
          className="size-full object-cover"
          loading="lazy"
        />
      )}
      {isVideo && (
        <video src={m.file_url} className="size-full object-cover" muted preload="metadata" />
      )}
      <div className="absolute inset-x-0 bottom-0 flex items-center gap-1 bg-black/60 px-1.5 py-1 text-[11px] text-white">
        <Icon className="size-3 shrink-0" />
        <span className="truncate">{m.caption || KIND_LABELS[m.kind]}</span>
      </div>
      {/* Always shown on a phone (no hover there); the touch area is 40 px, the chip inside stays small. */}
      <button
        type="button"
        onClick={onRemove}
        className="group/remove absolute top-0 right-0 flex size-[40px] items-center justify-center outline-none md:hidden md:size-8 md:group-hover:flex md:focus-visible:flex"
        title="Удалить"
        aria-label="Удалить файл"
      >
        <span className="rounded bg-black/60 p-1 text-white group-hover/remove:bg-destructive group-focus-visible/remove:bg-destructive">
          <Trash2 className="size-3.5" />
        </span>
      </button>
    </div>
  );
}
