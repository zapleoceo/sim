import type { GenerationScene, Media } from '@/lib/types';

/** The shortest horizontal finger move (px) that turns the page. */
export const SWIPE_MIN_PX = 50;

/** The next index in a gallery of `length` items, wrapping around at both ends. */
export function stepIndex(index: number, delta: number, length: number): number {
  if (length <= 0) return 0;
  return (((index + delta) % length) + length) % length;
}

/**
 * A finger moved by (dx, dy): +1 = the next item (swipe to the left), -1 = the previous one,
 * 0 = too short or mostly vertical (scrolling the caption, not paging).
 */
export function swipeStep(dx: number, dy: number): -1 | 0 | 1 {
  if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < Math.abs(dy) * 1.5) return 0;
  return dx < 0 ? 1 : -1;
}

/** Whose file it is, in words. */
export function originLabel(media: Pick<Media, 'source'>): string {
  if (media.source === 'generated') return 'Сгенерировано моделью';
  if (media.source === 'library') return 'Загружено в медиатеку';
  return 'Прислал клиент';
}

const LIGHT: Record<string, string> = {
  morning_daylight: 'мягкий утренний свет',
  daylight: 'дневной свет',
  golden_hour: 'низкое вечернее солнце',
  evening_lamp: 'вечер, свет лампы',
  night: 'ночь, одна лампа',
};

const SHOT: Record<string, string> = {
  front_selfie: 'селфи на фронтальную камеру',
  mirror_selfie: 'селфи в зеркале',
  taken_by_other: 'снимает кто-то рядом',
};

/** The scene of a generated picture as «label — value» rows, only what it keeps. */
export function sceneRows(scene: GenerationScene | null | undefined): [string, string][] {
  if (!scene) return [];
  const rows: [string, string | undefined][] = [
    ['Комната', scene.room],
    ['Место', scene.place],
    ['Чем занята', scene.activity],
    ['Одежда', scene.outfit],
    ['Свет', scene.light ? (LIGHT[scene.light] ?? scene.light) : undefined],
    ['Кадр', scene.shot ? (SHOT[scene.shot] ?? scene.shot) : undefined],
    ['Камера', scene.camera_reason],
    ['День', scene.day],
  ];
  return rows.filter((row): row is [string, string] => Boolean(row[1]?.trim()));
}

/** Screen-reader title of the open item: «Фото 3 из 12: подпись». */
export function lightboxTitle(media: Media, index: number, length: number): string {
  const what = media.kind === 'video' ? 'Видео' : 'Фото';
  return `${what} ${index + 1} из ${length}${media.caption ? `: ${media.caption}` : ''}`;
}
