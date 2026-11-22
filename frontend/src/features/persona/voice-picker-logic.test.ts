import { describe, expect, it } from 'vitest';
import type { Voice } from '@/lib/types';
import { filterVoices, formatSpeed, normalizeSpeed, selectedVoiceText, voiceTraits } from './voice-picker-logic';

const VOICES: Voice[] = [
  { id: 'Kore', label: 'Kore', gender: 'female', tone: 'уверенный' },
  { id: 'Puck', label: 'Puck', gender: 'male', tone: 'бодрый' },
  { id: 'Aoede', label: 'Aoede', gender: 'female', tone: 'лёгкий, Бодрый' },
];

const ids = (list: Voice[]) => list.map((v) => v.id);

describe('filterVoices', () => {
  it('keeps the chosen gender', () => {
    expect(ids(filterVoices(VOICES, 'all', ''))).toEqual(['Kore', 'Puck', 'Aoede']);
    expect(ids(filterVoices(VOICES, 'female', ''))).toEqual(['Kore', 'Aoede']);
    expect(ids(filterVoices(VOICES, 'male', ''))).toEqual(['Puck']);
  });

  it('searches id, label and tone in any case, together with the gender', () => {
    expect(ids(filterVoices(VOICES, 'all', 'БОДР'))).toEqual(['Puck', 'Aoede']);
    expect(ids(filterVoices(VOICES, 'female', ' бодр '))).toEqual(['Aoede']);
    expect(ids(filterVoices(VOICES, 'all', 'kor'))).toEqual(['Kore']);
    expect(filterVoices(VOICES, 'male', 'kore')).toEqual([]);
  });
});

describe('selectedVoiceText', () => {
  it('names the voice with its tone and gender', () => {
    expect(selectedVoiceText(VOICES, 'Kore')).toBe('Kore — уверенный, женский');
    expect(voiceTraits({ gender: 'male', tone: '' })).toBe('мужской');
  });

  it('shows a saved voice the list does not know, and an empty choice', () => {
    expect(selectedVoiceText(VOICES, 'Charon')).toBe('Charon');
    expect(selectedVoiceText([], 'Kore')).toBe('Kore');
    expect(selectedVoiceText(VOICES, '')).toBe('не выбран');
  });
});

describe('speech speed', () => {
  it('rounds and clamps the slider value', () => {
    expect(normalizeSpeed(1.0500000000000003)).toBe(1.05);
    expect(normalizeSpeed(0.5)).toBe(0.8);
    expect(normalizeSpeed(3)).toBe(1.5);
    expect(normalizeSpeed(Number.NaN)).toBe(1);
  });

  it('shows it as a multiplier', () => {
    expect(formatSpeed(1.3)).toBe('×1.30');
    expect(formatSpeed(1)).toBe('×1.00');
  });
});
