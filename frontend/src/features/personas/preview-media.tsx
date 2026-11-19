import { useQuery } from '@tanstack/react-query';
import { personasApi } from '@/lib/api/personas';
import { qk } from '@/lib/query';

export function PreviewGalleryPhoto({ personaId }: { personaId: number }) {
  const gallery = useQuery({
    queryKey: qk.personaMedia(personaId),
    queryFn: () => personasApi.media.list(personaId),
    retry: false,
  });
  const photo = gallery.data?.find((item) => item.kind === 'photo'
    && Number.isSafeInteger(item.id) && item.id > 0);

  if (gallery.isPending) return <p role="status">Загружаем фото из галереи…</p>;
  if (gallery.isError) return <p role="alert">Не удалось открыть галерею персонажа.</p>;
  if (!photo) return <p>В галерее персонажа пока нет фото.</p>;

  return (
    <img
      src={`/api/personas/${personaId}/media/${photo.id}/file`}
      alt="Фото из галереи персонажа"
      className="max-h-48 max-w-full rounded-lg object-contain"
    />
  );
}

export function PreviewVideoCircle() {
  return (
    <div className="aspect-square w-48 max-w-full overflow-hidden rounded-full bg-black">
      <video
        src={`${import.meta.env.BASE_URL}preview-circle.mp4`}
        aria-label="Тестовый видеокружок"
        className="size-full object-cover"
        controls
        playsInline
        preload="metadata"
      />
    </div>
  );
}
