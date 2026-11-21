import { describe, expect, it } from 'vitest';
import type { BrokerModel } from '@/lib/types';
import { MODEL_PURPOSES, matchesPurpose, modelOptions, type ModelPurpose } from './model-filter';

const model = (id: string, input: string[], output: string[], capabilities: string[] = [], available: boolean | null = true): BrokerModel => ({
  id, available, input_modalities: input, output_modalities: output, capabilities,
});

const CATALOG: BrokerModel[] = [
  model('gpt-text', ['text'], ['text'], ['json']),
  model('gemini-vision', ['text', 'image', 'video', 'audio'], ['text']),
  model('whisper', ['audio'], ['text']),
  model('gemini-tts', ['text'], ['audio']),
  model('imagen', ['text', 'image'], ['image']),
  model('veo', ['text', 'image'], ['video']),
  model('gemini-live', ['audio'], ['audio', 'text'], ['live']),
  model('weird-live', ['text'], ['text'], ['live'], false),
];

const fit = (purpose: ModelPurpose): string[] => CATALOG.filter((m) => matchesPurpose(m, purpose)).map((m) => m.id);

describe('matchesPurpose', () => {
  it('text → text for replies, guard and memory', () => {
    for (const p of ['reply', 'guard', 'memory'] as const) {
      expect(fit(p)).toEqual(['gpt-text', 'gemini-vision', 'weird-live']);
    }
  });

  it('vision needs image or video input', () => {
    expect(fit('vision')).toEqual(['gemini-vision', 'imagen', 'veo']);
  });

  it('transcribe needs audio in and text out', () => {
    expect(fit('transcribe')).toEqual(['gemini-vision', 'whisper', 'gemini-live']);
  });

  it('tts, image and video go by output modality', () => {
    expect(fit('tts')).toEqual(['gemini-tts', 'gemini-live']);
    expect(fit('image')).toEqual(['imagen']);
    expect(fit('video')).toEqual(['veo']);
  });

  it('live: the live capability or audio in and out', () => {
    expect(fit('live')).toEqual(['gemini-live', 'weird-live']);
  });

  it('tolerates missing modality lists', () => {
    const bare = { id: 'x', available: true } as unknown as BrokerModel;
    expect(matchesPurpose(bare, 'reply')).toBe(false);
  });

  it('covers every persona model field', () => {
    expect(MODEL_PURPOSES.map((p) => p.field)).toEqual([
      'model_reply', 'model_guard', 'model_vision', 'model_transcribe', 'model_memory',
      'model_tts', 'model_image', 'model_video', 'model_live',
    ]);
  });
});

describe('modelOptions', () => {
  it('lists available models first and marks availability', () => {
    const options = modelOptions(CATALOG, 'live', '');
    expect(options.map((o) => o.value)).toEqual(['gemini-live', 'weird-live']);
    expect(options[1]?.label).toBe('weird-live — недоступна');
  });

  it('keeps the current value when it does not fit the purpose', () => {
    const options = modelOptions(CATALOG, 'image', 'gpt-text');
    expect(options[0]).toMatchObject({ value: 'gpt-text', outsideFilter: true });
    expect(options[0]?.label).toContain('не подходит');
    expect(options.map((o) => o.value)).toEqual(['gpt-text', 'imagen']);
  });

  it('keeps the current value when it is gone from the catalog', () => {
    const options = modelOptions(CATALOG, 'video', 'old-video-model');
    expect(options[0]).toMatchObject({ value: 'old-video-model', outsideFilter: true, available: null });
    expect(options[0]?.label).toContain('нет в каталоге');
  });

  it('does not duplicate a current value that fits', () => {
    expect(modelOptions(CATALOG, 'video', 'veo').map((o) => o.value)).toEqual(['veo']);
  });
});
