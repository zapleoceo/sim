import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { Media } from '@/lib/types';
import { MediaLightbox } from './media-lightbox';

function media(id: number, patch: Partial<Media> = {}): Media {
  return {
    id, kind: 'photo', mime: 'image/png', url: `/api/media/${id}/file`, caption: `фото ${id}`, tags: [], description: '',
    source: 'generated', is_reference: false, width: 1024, height: 1536, duration_s: null, file_name: '',
    created_at: '2026-11-22T15:34:18Z', scene: null, ...patch,
  };
}

const ITEMS: Media[] = [
  media(26, {
    caption: 'Занимаюсь английским на кухне', tags: ['кухня', 'вечер'],
    scene: { room: 'кухня', place: 'дом, кухня', activity: 'ужинает', outfit: 'кремовый свитер', light: 'golden_hour', room_ref: true },
  }),
  media(27, { kind: 'video', mime: 'video/mp4', caption: 'кружок', duration_s: 8 }),
  media(2, { source: 'library', caption: '' }),
];

function Gallery({ start = 0, onClose = () => undefined }: { start?: number; onClose?: () => void }) {
  const [index, setIndex] = useState<number | null>(start);
  return (
    <MediaLightbox
      items={ITEMS}
      index={index}
      onIndexChange={setIndex}
      onClose={() => {
        setIndex(null);
        onClose();
      }}
      actions={(m) => <button type="button">действие {m.id}</button>}
    />
  );
}

/** A finger touch: jsdom has no Touch objects, so the touch list is set on a plain event. */
function touch(el: Element, type: 'touchstart' | 'touchend', clientX: number, clientY: number): void {
  const event = new Event(type, { bubbles: true });
  Object.defineProperty(event, type === 'touchstart' ? 'touches' : 'changedTouches', { value: [{ clientX, clientY }] });
  fireEvent(el, event);
}

const title = (): string => screen.getByRole('dialog').querySelector('h2')?.textContent ?? '';

describe('media lightbox', () => {
  it('shows the picture with its caption, tags, date, origin and generation scene', () => {
    render(<Gallery />);
    const dialog = screen.getByRole('dialog');
    expect(title()).toBe('Фото 1 из 3: Занимаюсь английским на кухне');
    expect(within(dialog).getByRole('img')).toHaveAttribute('src', '/api/media/26/file');
    const about = within(dialog).getByRole('complementary', { name: 'О файле' });
    expect(within(about).getByText('Занимаюсь английским на кухне')).toBeInTheDocument();
    expect(within(about).getByText('#кухня')).toBeInTheDocument();
    expect(within(about).getByText('Сгенерировано моделью')).toBeInTheDocument();
    expect(within(about).getByText(/2026/)).toBeInTheDocument();
    const scene = within(about).getByRole('region', { name: 'Сцена генерации' });
    expect(within(scene).getByText('кремовый свитер')).toBeInTheDocument();
    expect(within(scene).getByText('низкое вечернее солнце')).toBeInTheDocument();
    expect(within(scene).getByText('эталон комнаты')).toBeInTheDocument();
    expect(within(about).getByRole('button', { name: 'действие 26' })).toBeInTheDocument();
    expect(within(dialog).getByText('1 / 3')).toBeInTheDocument();
  });

  it('pages with the arrows and the keys, wrapping around', async () => {
    render(<Gallery />);
    await userEvent.click(screen.getByRole('button', { name: 'Следующее' }));
    expect(title()).toBe('Видео 2 из 3: кружок');
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowRight' });
    expect(title()).toBe('Фото 3 из 3');
    expect(screen.getByText('Загружено в медиатеку')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Сцена генерации' })).toBeNull();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowRight' });
    expect(title()).toMatch(/^Фото 1 из 3/);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowLeft' });
    expect(title()).toBe('Фото 3 из 3');
    await userEvent.click(screen.getByRole('button', { name: 'Предыдущее' }));
    expect(title()).toBe('Видео 2 из 3: кружок');
  });

  it('plays a video inside, with its controls', () => {
    const { baseElement } = render(<Gallery start={1} />);
    const video = baseElement.querySelector('[role="dialog"] video');
    expect(video).not.toBeNull();
    expect(video).toHaveAttribute('src', '/api/media/27/file');
    expect(video?.hasAttribute('controls')).toBe(true);
  });

  it('turns the page with a swipe on a phone, not with a vertical scroll', () => {
    render(<Gallery />);
    const stage = screen.getByTestId('lightbox-stage');
    touch(stage, 'touchstart', 300, 200);
    touch(stage, 'touchend', 120, 210);
    expect(title()).toMatch(/^Видео 2 из 3/);
    touch(stage, 'touchstart', 100, 100);
    touch(stage, 'touchend', 120, 400);
    expect(title()).toMatch(/^Видео 2 из 3/);
    touch(stage, 'touchstart', 100, 200);
    touch(stage, 'touchend', 300, 200);
    expect(title()).toMatch(/^Фото 1 из 3/);
  });

  it('Esc and the close button close it', async () => {
    const onClose = vi.fn();
    render(<Gallery onClose={onClose} />);
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onClose).toHaveBeenCalledTimes(1);

    render(<Gallery onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('one file: no paging controls', () => {
    render(<MediaLightbox items={[ITEMS[0]!]} index={0} onIndexChange={() => undefined} onClose={() => undefined} />);
    expect(screen.queryByRole('button', { name: 'Следующее' })).toBeNull();
    expect(screen.queryByText('1 / 1')).toBeNull();
  });
});
