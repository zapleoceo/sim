import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Media } from '@/lib/types';
import { MediaView, ringOffset } from './media-view';

const note: Media = {
  id: 7, kind: 'video', mime: 'video/mp4', url: '/api/media/7/file', caption: '', tags: [],
  description: 'Видно: Марина на кухне. Говорит: привет', source: 'generated', is_reference: false,
  width: 640, height: 640, duration_s: 8, file_name: 'video_note.mp4', created_at: '2026-11-21T18:44:46Z',
} as Media;

describe('video note («кружок»)', () => {
  it('is a circle without player controls or a seek bar', () => {
    const { container } = render(<MediaView media={note} messageKind="video_note" />);
    const video = container.querySelector('video');
    expect(video).not.toBeNull();
    expect(video?.hasAttribute('controls')).toBe(false);
    expect(video?.className).toContain('rounded-full');
    expect(screen.getByRole('button', { name: 'Кружок: воспроизвести' })).toBeInTheDocument();
    expect(screen.getByText('0:08')).toBeInTheDocument();
  });

  it('plays on click', () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    render(<MediaView media={note} messageKind="video_note" />);
    fireEvent.click(screen.getByRole('button', { name: /Кружок/ }));
    expect(play).toHaveBeenCalled();
    play.mockRestore();
  });

  it('an ordinary video keeps its controls', () => {
    const { container } = render(<MediaView media={note} messageKind="video" />);
    expect(container.querySelector('video')?.hasAttribute('controls')).toBe(true);
  });

  it('the ring fills with progress', () => {
    const full = ringOffset(0, 8);
    expect(ringOffset(4, 8)).toBeCloseTo(full / 2);
    expect(ringOffset(8, 8)).toBe(0);
    expect(ringOffset(3, 0)).toBe(full);
  });
});
