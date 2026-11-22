import { Loader2, Play, Square } from 'lucide-react';
import { useState } from 'react';
import { SearchInput } from '@/components/search-input';
import { Segmented } from '@/components/segmented';
import { Button } from '@/components/ui/button';
import type { Voice } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useVoiceSample } from './use-voice-sample';
import { VOICE_FILTERS, filterVoices, selectedVoiceText, voiceTraits, type VoiceFilter } from './voice-picker-logic';

export interface VoicePickerProps {
  /** Id of the list (labels point at it). */
  id: string;
  value: string;
  onChange: (voiceId: string) => void;
  voices: readonly Voice[];
  personaId: number;
  /** The persona's speech speed: samples play at it. */
  speed: number;
}

/**
 * «Голос» of the persona: gender chips and a search above a list of voices; a row click chooses the
 * voice, ▶ plays its sample (as this persona would say it, at her speed). The chosen voice is
 * always named above the list, even when the filter hides it or the list does not know it.
 */
export function VoicePicker({ id, value, onChange, voices, personaId, speed }: VoicePickerProps) {
  const [filter, setFilter] = useState<VoiceFilter>('all');
  const [query, setQuery] = useState('');
  const sample = useVoiceSample(personaId, speed);
  const shown = filterVoices(voices, filter, query);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="text-sm">
        <span className="text-muted-foreground">Выбран: </span>
        <span className="font-medium">{selectedVoiceText(voices, value)}</span>
      </p>
      {/* The picker lives inside the persona form: Enter in the search must not save the form. */}
      <div
        className="flex flex-wrap items-center gap-2"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && e.target instanceof HTMLInputElement) e.preventDefault();
        }}
      >
        <Segmented label="Пол голоса" value={filter} options={VOICE_FILTERS} onChange={setFilter} />
        <SearchInput
          value={query}
          onChange={setQuery}
          label="Поиск голоса"
          placeholder="Имя или тон"
          className="min-w-40 flex-1"
        />
      </div>
      <div
        id={id}
        role="radiogroup"
        aria-label="Голос"
        className="flex max-h-72 flex-col overflow-y-auto rounded-md border scrollbar-thin"
      >
        {shown.length === 0 && (
          <p className="px-3 py-4 text-center text-sm text-muted-foreground">
            {voices.length === 0 ? 'Список голосов не загрузился' : 'Ничего не нашлось'}
          </p>
        )}
        {shown.map((voice) => {
          const selected = voice.id === value;
          const playing = sample.playing === voice.id;
          const loading = sample.loading === voice.id;
          const error = sample.error?.voiceId === voice.id ? sample.error.text : null;
          const name = voice.label || voice.id;
          return (
            <div key={voice.id} className={cn('flex flex-col border-b last:border-b-0', selected && 'bg-accent text-accent-foreground')}>
              <div className="flex items-center gap-1 pr-1">
                <button
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => onChange(voice.id)}
                  className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left text-sm outline-none hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      'flex size-4 flex-none items-center justify-center rounded-full border',
                      selected ? 'border-primary' : 'border-input',
                    )}
                  >
                    {selected && <span className="size-2 rounded-full bg-primary" />}
                  </span>
                  <span className="min-w-0 truncate">
                    <span className="font-medium">{name}</span>
                    {voiceTraits(voice) && <span className="text-muted-foreground"> — {voiceTraits(voice)}</span>}
                  </span>
                </button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={playing ? `Остановить ${name}` : `Послушать ${name}`}
                  disabled={loading}
                  onClick={() => (playing ? sample.stop() : void sample.play(voice.id))}
                >
                  {loading ? <Loader2 className="animate-spin" /> : playing ? <Square /> : <Play />}
                </Button>
              </div>
              {error && <p role="alert" className="px-3 pb-2 text-xs text-destructive">{error}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
