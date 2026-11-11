import { humanizeChannelError } from '@/lib/channel-error';
import { formatDateTime, isoToLocalInput, localInputToIso } from '@/lib/format';
import type { ContentIn, ContentKind, ContentPost, ContentStatus, Persona } from '@/lib/models';

export const CAPTION_MAX = 2200;

export const CONTENT_COLUMNS: { status: ContentStatus; label: string }[] = [
  { status: 'draft', label: 'Черновики' },
  { status: 'approved', label: 'Одобрено' },
  { status: 'scheduled', label: 'Запланировано' },
  { status: 'published', label: 'Опубликовано' },
  { status: 'failed', label: 'Ошибка' },
];

export const KIND_LABELS: Record<ContentKind, string> = { photo: 'Фото', video: 'Видео', reel: 'Reels', story: 'Сторис' };
export const CONTENT_KINDS = Object.keys(KIND_LABELS) as ContentKind[];

export function statusLabel(status: ContentStatus | undefined): string {
  return CONTENT_COLUMNS.find((c) => c.status === status)?.label ?? '';
}

/** One URL per line; blank lines are ignored. */
export function parseMediaUrls(text: string): string[] {
  return text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
}

/** Every media line must be an http(s) or root-relative URL. */
export function mediaUrlsValid(text: string): boolean {
  return parseMediaUrls(text).every((u) => /^(https?:\/\/|\/)\S+$/i.test(u));
}

export interface PostCardVm {
  post: ContentPost;
  kind: string;
  persona: string;
  when: string;
  error: string;
}

export interface ContentColumn { status: ContentStatus; label: string; posts: PostCardVm[] }

/** Status tabs with their posts; the «Ошибка» tab only appears when something failed. */
export function contentColumns(posts: ContentPost[], personas: Persona[]): ContentColumn[] {
  return CONTENT_COLUMNS.map((c) => ({
    ...c,
    posts: posts
      .filter((p) => p.status === c.status)
      .map((post) => ({
        post,
        kind: KIND_LABELS[post.kind] ?? post.kind,
        persona: personas.find((p) => p.id === post.persona_id)?.name ?? '',
        when: formatDateTime(post.scheduled_at ?? post.published_at),
        error: humanizeChannelError(post.error),
      })),
  })).filter((c) => c.posts.length > 0 || c.status !== 'failed');
}

export interface ContentFormValues {
  persona_id: number | null;
  channel_id: number | null;
  kind: ContentKind;
  caption: string;
  media: string;
  /** datetime-local value, local time */
  scheduled_at: string;
}

export function contentFormDefaults(post: ContentPost | null, personas: Persona[]): ContentFormValues {
  return {
    persona_id: post?.persona_id ?? personas[0]?.id ?? null,
    channel_id: post?.channel_id ?? null,
    kind: post?.kind ?? 'photo',
    caption: post?.caption ?? '',
    media: (post?.media_urls ?? []).join('\n'),
    scheduled_at: isoToLocalInput(post?.scheduled_at),
  };
}

export function contentFromForm(v: ContentFormValues): ContentIn {
  return {
    persona_id: v.persona_id as number,
    channel_id: v.channel_id,
    kind: v.kind,
    caption: v.caption,
    media_urls: parseMediaUrls(v.media),
    scheduled_at: localInputToIso(v.scheduled_at),
  };
}
