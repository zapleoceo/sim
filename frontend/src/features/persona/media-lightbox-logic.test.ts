import { describe, expect, it } from 'vitest';
import type { Media } from '@/lib/types';
import { lightboxTitle, originLabel, sceneRows, stepIndex, swipeStep, SWIPE_MIN_PX } from './media-lightbox-logic';

describe('paging', () => {
  it('wraps around at both ends', () => {
    expect(stepIndex(0, 1, 3)).toBe(1);
    expect(stepIndex(2, 1, 3)).toBe(0);
    expect(stepIndex(0, -1, 3)).toBe(2);
    expect(stepIndex(0, 1, 0)).toBe(0);
  });

  it('a swipe to the left is the next item, a short or vertical move is nothing', () => {
    expect(swipeStep(-SWIPE_MIN_PX - 10, 5)).toBe(1);
    expect(swipeStep(SWIPE_MIN_PX + 10, -5)).toBe(-1);
    expect(swipeStep(-20, 0)).toBe(0);
    expect(swipeStep(-80, 120)).toBe(0);
  });
});

describe('what is shown next to the picture', () => {
  it('whose file it is', () => {
    expect(originLabel({ source: 'generated' })).toBe('Сгенерировано моделью');
    expect(originLabel({ source: 'library' })).toBe('Загружено в медиатеку');
    expect(originLabel({ source: 'incoming' })).toBe('Прислал клиент');
  });

  it('the scene of a generation, only the keys it keeps, in words', () => {
    expect(sceneRows({
      room: 'кухня', place: 'дом, кухня', activity: 'ужинает', outfit: 'кремовый свитер',
      light: 'golden_hour', shot: 'front_selfie', day: '2026-11-22', camera_reason: ' ',
    })).toEqual([
      ['Комната', 'кухня'],
      ['Место', 'дом, кухня'],
      ['Чем занята', 'ужинает'],
      ['Одежда', 'кремовый свитер'],
      ['Свет', 'низкое вечернее солнце'],
      ['Кадр', 'селфи на фронтальную камеру'],
      ['День', '2026-11-22'],
    ]);
    // An older generation keeps fewer keys; an unknown value is shown as it is.
    expect(sceneRows({ place: 'дома', light: 'neon' })).toEqual([['Место', 'дома'], ['Свет', 'neon']]);
    expect(sceneRows(null)).toEqual([]);
  });

  it('the title for screen readers', () => {
    const photo = { kind: 'photo', caption: 'на кухне' } as Media;
    expect(lightboxTitle(photo, 2, 12)).toBe('Фото 3 из 12: на кухне');
    expect(lightboxTitle({ ...photo, kind: 'video', caption: '' }, 0, 1)).toBe('Видео 1 из 1');
  });
});
