import type { ContentPost, Persona } from '@/lib/models';
import {
  contentColumns, contentFormDefaults, contentFromForm, mediaUrlsValid, parseMediaUrls, statusLabel,
} from './content-logic';

const persona = { id: 3, name: 'Марина' } as Persona;
const post: ContentPost = {
  id: 1, persona_id: 3, channel_id: null, kind: 'reel', caption: 'Привет', media_urls: ['https://cdn/a.jpg', '/media/b.mp4'],
  status: 'draft', scheduled_at: null, published_at: null, error: null,
};

describe('content logic', () => {
  it('parses media URLs one per line', () => {
    expect(parseMediaUrls(' https://a/1.jpg \n\n/media/2.jpg\r\n')).toEqual(['https://a/1.jpg', '/media/2.jpg']);
    expect(mediaUrlsValid('https://a/1.jpg\n/media/2.jpg')).toBe(true);
    expect(mediaUrlsValid('')).toBe(true);
    expect(mediaUrlsValid('photo.jpg')).toBe(false);
    expect(mediaUrlsValid('javascript:alert(1)')).toBe(false);
  });

  it('groups posts by status and hides the failed tab when empty', () => {
    const cols = contentColumns([post], [persona]);
    expect(cols.map((c) => c.status)).toEqual(['draft', 'approved', 'scheduled', 'published']);
    expect(cols[0].posts[0]).toMatchObject({ kind: 'Reels', persona: 'Марина' });
    const failed = contentColumns([{ ...post, status: 'failed', error: 'LoginRequired: x' }], []);
    expect(failed.at(-1)!.status).toBe('failed');
    expect(failed.at(-1)!.posts[0].error).toMatch(/Сессия истекла/);
    expect(statusLabel('scheduled')).toBe('Запланировано');
  });

  it('round-trips the form to the API contract', () => {
    const v = contentFormDefaults(post, [persona]);
    expect(v).toMatchObject({ persona_id: 3, kind: 'reel', media: 'https://cdn/a.jpg\n/media/b.mp4', scheduled_at: '' });
    expect(contentFromForm(v)).toEqual({
      persona_id: 3, channel_id: null, kind: 'reel', caption: 'Привет', media_urls: ['https://cdn/a.jpg', '/media/b.mp4'], scheduled_at: null,
    });
    const when = contentFromForm({ ...v, scheduled_at: '2026-11-11T14:05' }).scheduled_at;
    expect(when).toBe(new Date('2026-11-11T14:05').toISOString());
  });

  it('defaults a new post to the first persona', () => {
    expect(contentFormDefaults(null, [persona])).toMatchObject({ persona_id: 3, kind: 'photo', caption: '' });
    expect(contentFormDefaults(null, []).persona_id).toBeNull();
  });
});
