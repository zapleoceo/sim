import { useMutation } from '@tanstack/react-query';
import { Circle } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { personasApi } from '@/lib/api/personas';

/**
 * Owner only: ask for an AI video note of the persona. The backend sends it to the single
 * account from the setting «Кружочки ИИ» and nowhere else; errors (no recipient set, no
 * dialog) arrive as the standard API error toast.
 */
export function AiVideoNote({ personaId }: { personaId: number }) {
  const [prompt, setPrompt] = useState('');
  const send = useMutation({
    mutationFn: () => personasApi.videoNote(personaId, prompt.trim()),
    onSuccess: (res) => {
      toast.success(
        res.cached
          ? 'Такой кружочек уже есть: отправляется из кеша'
          : 'Кружочек генерируется и придёт в Telegram разрешённому аккаунту',
      );
    },
  });

  return (
    <section aria-labelledby="ai-video-note-h" className="flex flex-col gap-3 rounded-xl border bg-card p-4 shadow-xs sm:p-5">
      <h3 id="ai-video-note-h" className="text-base font-semibold">Кружочек ИИ</h3>
      <p className="text-sm text-muted-foreground">
        Видео генерирует ИИ. Оно уходит только на аккаунт из настройки «Кружочки ИИ», перед ним
        отправляется сообщение «Сгенерировано ИИ». Одинаковый запрос берётся из кеша.
      </p>
      <Textarea
        aria-label="Описание кружочка"
        placeholder="Что в кадре и что она говорит"
        value={prompt}
        maxLength={4000}
        onChange={(e) => setPrompt(e.target.value)}
        className="max-h-48"
      />
      <Button
        className="self-start"
        disabled={send.isPending || prompt.trim() === ''}
        onClick={() => send.mutate()}
      >
        <Circle />
        Создать и отправить
      </Button>
    </section>
  );
}
