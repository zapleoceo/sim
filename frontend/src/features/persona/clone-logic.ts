import type { ClonePart } from '@/lib/types';

export interface ClonePartInfo {
  part: ClonePart;
  label: string;
  hint: string;
  /** Ticked when the dialog opens. */
  checked: boolean;
}

/**
 * What «Клонировать» may copy, in the order of the persona page (docs/personas.md). Channels,
 * clients and chats, planned days, generated media and runs are never copied.
 */
export const CLONE_PARTS: readonly ClonePartInfo[] = [
  { part: 'basics', label: 'Основное', hint: 'Пол, язык, часовой пояс, дата рождения.', checked: true },
  { part: 'prompt', label: 'Промт', hint: 'Кто она, характер, стиль, запреты.', checked: true },
  { part: 'agents', label: 'Агенты', hint: 'Модели, ключи, промты и лимиты всех агентов.', checked: true },
  { part: 'voice', label: 'Голос и фон', hint: 'Голос, скорость, манера речи, фон в голосовых, запись звонков и срок её хранения.', checked: true },
  { part: 'guard', label: 'Правила Охранника', hint: 'Список запретов, которые Охранник не пропускает.', checked: true },
  { part: 'appearance', label: 'Внешность', hint: 'Описание внешности для фото и видео.', checked: true },
  { part: 'world', label: 'Мир', hint: 'Распорядок, люди, увлечения, места.', checked: true },
  { part: 'arcs', label: 'Истории', hint: 'Сюжеты её жизни со стадией и статусом.', checked: true },
  { part: 'media', label: 'Медиатека', hint: 'Фото и видео с подписями, тегами и референсами; файлы копируются.', checked: true },
  { part: 'switches', label: 'Переключатели', hint: 'ИИ, голосовые, фото, видео, звонки, пауза перед ответом.', checked: true },
  { part: 'canon', label: 'Канон', hint: 'Факты о себе, которые она придумала в чатах. Обычно новому персонажу не нужны.', checked: false },
];

export const defaultParts = (): Set<ClonePart> => new Set(CLONE_PARTS.filter((p) => p.checked).map((p) => p.part));

/** The parts in page order (the request does not depend on the order of clicks). */
export function orderedParts(selected: ReadonlySet<ClonePart>): ClonePart[] {
  return CLONE_PARTS.map((p) => p.part).filter((part) => selected.has(part));
}

export function cloneNameError(name: string, takenNames: readonly string[]): string | null {
  const clean = name.trim().replace(/\s+/g, ' ');
  if (!clean) return 'Укажите имя';
  if (clean.length > 120) return 'Не длиннее 120 символов';
  if (takenNames.some((n) => n.toLowerCase() === clean.toLowerCase())) return 'Персонаж с таким именем уже есть';
  return null;
}
