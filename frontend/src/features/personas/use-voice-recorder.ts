import { useCallback, useEffect, useRef, useState } from 'react';

export interface VoiceClip { blob: Blob; filename: string }

/** Container the browser records into: Opus in WebM (Chrome, Firefox) or MP4/AAC (Safari). */
export function recordingType(isSupported: (type: string) => boolean): { mime: string; ext: string } {
  for (const [mime, ext] of [['audio/webm;codecs=opus', 'webm'], ['audio/ogg;codecs=opus', 'ogg'], ['audio/mp4', 'm4a']] as const) {
    if (isSupported(mime)) return { mime, ext };
  }
  return { mime: '', ext: 'webm' };
}

export function canRecord(): boolean {
  return typeof window !== 'undefined' && typeof window.MediaRecorder !== 'undefined'
    && Boolean(navigator.mediaDevices?.getUserMedia);
}

/** Microphone recording: `start`, then `stop` resolves with the clip (null if nothing was recorded). */
export function useVoiceRecorder() {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const type = useRef({ mime: '', ext: 'webm' });

  const release = useCallback(() => {
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
    recorder.current = null;
  }, []);

  useEffect(() => release, [release]);

  const start = useCallback(async (): Promise<boolean> => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      type.current = recordingType((t) => MediaRecorder.isTypeSupported(t));
      const rec = new MediaRecorder(stream, type.current.mime ? { mimeType: type.current.mime } : undefined);
      chunks.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.current.push(e.data);
      };
      rec.start();
      recorder.current = rec;
      setRecording(true);
      return true;
    } catch (e) {
      console.error('[voice recorder]', e);
      setError('Нет доступа к микрофону');
      return false;
    }
  }, []);

  const stop = useCallback(
    () =>
      new Promise<VoiceClip | null>((resolve) => {
        const rec = recorder.current;
        if (!rec) {
          resolve(null);
          return;
        }
        rec.onstop = () => {
          const blob = new Blob(chunks.current, { type: rec.mimeType || type.current.mime || 'audio/webm' });
          release();
          setRecording(false);
          resolve(blob.size > 0 ? { blob, filename: `voice.${type.current.ext}` } : null);
        };
        rec.stop();
      }),
    [release],
  );

  return { recording, error, start, stop };
}
