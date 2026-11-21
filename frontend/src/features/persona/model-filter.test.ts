import { describe, expect, it } from 'vitest';
import type { BrokerModel, ModelMatch } from '@/lib/types';
import { matchesFilter, modelOptions } from './model-filter';

const model = (id: string, input: string[], output: string[], capabilities: string[] = [], available: boolean | null = true): BrokerModel => ({
  id, available, input_modalities: input, output_modalities: output, capabilities,
});

const match = (input: string[] = [], output: string[] = [], capabilities: string[] = []): ModelMatch => ({ input, output, capabilities });

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

// The registry filters of sim/domain/agents.py.
const TEXT = [match(['text'], ['text'])];
const SEES = [match(['image']), match(['video'])];
const HEARS = [match(['audio'], ['text'])];
const LIVE = [match([], [], ['live']), match(['audio'], ['audio'])];

const fit = (filter: ModelMatch[]): string[] => CATALOG.filter((m) => matchesFilter(m, filter)).map((m) => m.id);

describe('matchesFilter', () => {
  it('text → text for the companion, guard, planner and the other text agents', () => {
    expect(fit(TEXT)).toEqual(['gpt-text', 'gemini-vision', 'weird-live']);
  });

  it('any alternative is enough: vision takes image or video input', () => {
    expect(fit(SEES)).toEqual(['gemini-vision', 'imagen', 'veo']);
  });

  it('every listed modality is required', () => {
    expect(fit(HEARS)).toEqual(['gemini-vision', 'whisper', 'gemini-live']);
    expect(fit([match([], ['audio'])])).toEqual(['gemini-tts', 'gemini-live']);
    expect(fit([match([], ['image'])])).toEqual(['imagen']);
  });

  it('capabilities count too', () => {
    expect(fit(LIVE)).toEqual(['gemini-live', 'weird-live']);
  });

  it('tolerates missing modality lists and an empty filter', () => {
    const bare = { id: 'x', available: true } as unknown as BrokerModel;
    expect(matchesFilter(bare, TEXT)).toBe(false);
    expect(matchesFilter(bare, [match()])).toBe(true);
    expect(matchesFilter(bare, [])).toBe(false);
  });
});

describe('modelOptions', () => {
  it('lists available models first and marks availability', () => {
    const options = modelOptions(CATALOG, LIVE, '');
    expect(options.map((o) => o.value)).toEqual(['gemini-live', 'weird-live']);
    expect(options[1]?.label).toBe('weird-live — недоступна');
  });

  it('keeps the current value when it does not fit the agent', () => {
    const options = modelOptions(CATALOG, [match([], ['image'])], 'gpt-text');
    expect(options[0]).toMatchObject({ value: 'gpt-text', outsideFilter: true });
    expect(options[0]?.label).toContain('не подходит');
    expect(options.map((o) => o.value)).toEqual(['gpt-text', 'imagen']);
  });

  it('keeps the current value when it is gone from the catalog', () => {
    const options = modelOptions(CATALOG, [match([], ['video'])], 'old-video-model');
    expect(options[0]).toMatchObject({ value: 'old-video-model', outsideFilter: true, available: null });
    expect(options[0]?.label).toContain('нет в каталоге');
  });

  it('does not duplicate a current value that fits', () => {
    expect(modelOptions(CATALOG, [match([], ['video'])], 'veo').map((o) => o.value)).toEqual(['veo']);
  });
});
