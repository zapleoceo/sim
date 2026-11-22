import { useEffect, useRef, useState } from 'react';
import { errorMessage } from '@/lib/api';
import { personaApi } from './api';

export interface VoiceSampleState {
  /** The voice playing now. */
  playing: string | null;
  /** The voice whose sample is being fetched: the first play may take a few seconds. */
  loading: string | null;
  error: { voiceId: string; text: string } | null;
}

const IDLE: VoiceSampleState = { playing: null, loading: null, error: null };

/**
 * Voice samples of the picker through one shared `<audio>`: starting a sample stops the previous
 * one. A fetched sample is kept as an object URL until the picker goes away. It plays at the
 * persona's speech speed with the pitch kept, as her voice notes are sped up.
 */
export function useVoiceSample(personaId: number, speed: number) {
  const [audio] = useState(() => new Audio());
  const [state, setState] = useState<VoiceSampleState>(IDLE);
  const urls = useRef(new Map<string, string>());
  // Each start or stop takes a new turn; a slow fetch of an older turn is dropped.
  const turn = useRef(0);

  useEffect(() => {
    audio.defaultPlaybackRate = speed;
    audio.playbackRate = speed;
  }, [audio, speed]);

  useEffect(() => {
    const cache = urls.current;
    const onEnded = () => setState((s) => ({ ...s, playing: null }));
    audio.addEventListener('ended', onEnded);
    return () => {
      audio.removeEventListener('ended', onEnded);
      if (!audio.paused) audio.pause();
      for (const url of cache.values()) URL.revokeObjectURL(url);
      cache.clear();
    };
  }, [audio]);

  const play = async (voiceId: string): Promise<void> => {
    const mine = ++turn.current;
    if (!audio.paused) audio.pause();
    const key = `${personaId}:${voiceId}`;
    let url = urls.current.get(key);
    setState({ ...IDLE, loading: url ? null : voiceId });
    if (!url) {
      try {
        url = URL.createObjectURL(await personaApi.voiceSample(voiceId, personaId));
      } catch (e) {
        if (mine === turn.current) setState({ ...IDLE, error: { voiceId, text: `Не удалось озвучить: ${errorMessage(e)}` } });
        return;
      }
      urls.current.set(key, url);
    }
    if (mine !== turn.current) return;
    audio.src = url;
    // Loading a new source resets the rate to the default one.
    audio.defaultPlaybackRate = speed;
    audio.playbackRate = speed;
    audio.preservesPitch = true;
    try {
      await audio.play();
    } catch {
      if (mine === turn.current) setState({ ...IDLE, error: { voiceId, text: 'Не удалось воспроизвести образец' } });
      return;
    }
    if (mine === turn.current) setState({ playing: voiceId, loading: null, error: null });
  };

  const stop = (): void => {
    turn.current += 1;
    audio.pause();
    setState(IDLE);
  };

  return { ...state, play, stop };
}
