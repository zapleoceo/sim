import { AlertCircle, FileText, Mic, Paperclip, RotateCcw, Send, Square } from 'lucide-react';
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { GuardBadge } from '@/components/guard-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { PreviewAttachment, PreviewVoice } from '@/lib/models';
import { cn } from '@/lib/utils';
import { isIdle, usePreviewChat } from './preview-chat';
import { canRecord, useVoiceRecorder } from './use-voice-recorder';

/** Files the Broker can read: voice/audio (transcription), images (vision), plain text. */
export const PREVIEW_ACCEPT = 'audio/*,image/*,.txt,.md,.csv,.json';

function AttachmentView({ a }: { a: PreviewAttachment }) {
  if (a.kind === 'voice' || a.kind === 'audio') {
    return <audio controls preload="metadata" src={a.media_url} className="h-9 max-w-full" aria-label={`Голосовое: ${a.name}`} />;
  }
  if (a.kind === 'image') {
    return <img src={a.media_url} alt={a.name} className="max-h-48 max-w-full rounded-lg object-contain" />;
  }
  return (
    <span className="flex items-center gap-1.5 text-xs">
      <FileText className="size-4" />
      {a.name}
    </span>
  );
}

function VoiceView({ v }: { v: PreviewVoice }) {
  if (v.media_url) {
    return <audio controls preload="metadata" src={v.media_url} className="h-9 max-w-full" aria-label="Голосовой ответ" />;
  }
  return <span className="text-[11px] text-warning">Голос не сгенерирован: {v.error}</span>;
}

/**
 * Trial replies without sending anything to a client; uses the saved persona settings.
 * Errors are shown inline with «Повторить» (the old admin swallowed them).
 * Render with `key={personaId}` so another persona starts a clean chat.
 */
export function PersonaPreview({ personaId }: { personaId: number }) {
  const chat = usePreviewChat(personaId);
  const recorder = useVoiceRecorder();
  const [text, setText] = useState('');
  const log = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const idle = isIdle(chat);

  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages.length, chat.busy, chat.uploading, chat.failure]);

  const submit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (chat.send(text)) setText('');
  };

  const attach = (e: ChangeEvent<HTMLInputElement>): void => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    void chat.sendFile(file, file.name, { caption: text }).then((sent) => {
      if (sent) setText('');
    });
  };

  const toggleRecording = async (): Promise<void> => {
    if (!recorder.recording) {
      await recorder.start();
      return;
    }
    const clip = await recorder.stop();
    if (clip) void chat.sendFile(clip.blob, clip.filename, { voice: true });
  };

  return (
    <section aria-labelledby="preview-h" className="flex min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="preview-h" className="text-base font-semibold">
          Проверить персонажа
        </h2>
        <Button variant="ghost" size="sm" onClick={chat.reset} disabled={!chat.messages.length || chat.busy}>
          Очистить
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">Пробный ответ без отправки клиенту. Проверяются сохранённые настройки.</p>

      <div
        ref={log}
        role="log"
        aria-live="polite"
        className="flex max-h-[28rem] min-h-52 flex-col gap-2 overflow-y-auto rounded-lg bg-muted/60 p-3"
      >
        {chat.messages.length === 0 && !chat.busy && (
          <p className="m-auto text-center text-sm text-muted-foreground">Напишите сообщение, как это сделал бы клиент.</p>
        )}
        {chat.messages.map((m, i) => (
          <div key={i} className={cn('flex flex-col gap-1', m.role === 'user' ? 'items-end' : 'items-start')}>
            {m.guard?.verdict === 'blocked' ? (
              <div className="max-w-[90%] rounded-2xl border border-dashed border-destructive bg-card px-3 py-2 text-sm text-destructive">
                Ответ заблокирован контролером и не был бы отправлен.
              </div>
            ) : (
              <div
                className={cn(
                  'flex max-w-[90%] flex-col gap-1.5 rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap [overflow-wrap:anywhere]',
                  m.role === 'user' ? 'bg-bubble-client' : 'bg-bubble-ai',
                  m.failed && 'opacity-70',
                )}
              >
                {m.attachment && <AttachmentView a={m.attachment} />}
                {m.voice && <VoiceView v={m.voice} />}
                <span className={cn((m.attachment || m.voice?.media_url) && 'text-xs text-muted-foreground')}>{m.text}</span>
              </div>
            )}
            <GuardBadge guard={m.guard} />
            {m.meta && <span className="text-[11px] text-muted-foreground">{m.meta}</span>}
            {m.requests && (
              <details className="max-w-full text-xs">
                <summary>Фактически отправленные запросы SIM → Broker</summary>
                <p>Генератор, повторы и контролёр раздельно. История и контекст показаны точно на момент запроса. Внутренние инструкции провайдера не доступны.</p>
                {m.requests.map((request, index) => (
                  <pre key={index} className="max-h-96 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere]">
                    {JSON.stringify(request, null, 2)}
                  </pre>
                ))}
              </details>
            )}
            {m.decision != null && (
              <details className="max-w-full text-xs">
                <summary className="cursor-pointer text-muted-foreground">Решение модели</summary>
                <pre className="mt-1 text-[11px] whitespace-pre-wrap [overflow-wrap:anywhere]">
                  {JSON.stringify(m.decision, null, 2)}
                </pre>
              </details>
            )}
          </div>
        ))}
        {chat.uploading && (
          <div className="flex justify-end">
            <div className="animate-pulse rounded-2xl bg-bubble-client px-3 py-2 text-sm text-muted-foreground">распознаю…</div>
          </div>
        )}
        {chat.busy && (
          <div className="flex">
            <div className="animate-pulse rounded-2xl bg-bubble-ai px-3 py-2 text-sm text-muted-foreground">печатает…</div>
          </div>
        )}
        {(chat.uploadError || recorder.error) && (
          <p role="alert" className="flex items-start gap-1.5 text-sm text-destructive">
            <AlertCircle className="mt-0.5 size-4 flex-none" />
            {chat.uploadError ? `Не удалось распознать: ${chat.uploadError}` : recorder.error}
          </p>
        )}
        {chat.failure && (
          <div
            role="alert"
            className="flex flex-col items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <span className="flex items-start gap-1.5">
              <AlertCircle className="mt-0.5 size-4 flex-none" />
              <span>Персонаж не ответил. {chat.failure.error}</span>
            </span>
            <Button variant="outline" size="sm" onClick={chat.retry}>
              <RotateCcw />
              Повторить
            </Button>
          </div>
        )}
      </div>

      <form className="flex gap-2" onSubmit={submit}>
        <input
          ref={fileInput}
          type="file"
          accept={PREVIEW_ACCEPT}
          className="hidden"
          onChange={attach}
          aria-label="Файл"
          data-testid="preview-file"
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-10 flex-none"
          disabled={!idle || recorder.recording}
          onClick={() => fileInput.current?.click()}
          aria-label="Прикрепить файл"
        >
          <Paperclip />
        </Button>
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={recorder.recording ? 'Идёт запись…' : 'Сообщение'}
          aria-label="Сообщение"
          autoComplete="off"
          className="h-10"
          disabled={recorder.recording}
        />
        {canRecord() && (
          <Button
            type="button"
            variant={recorder.recording ? 'destructive' : 'outline'}
            size="icon"
            className="size-10 flex-none"
            disabled={!idle}
            onClick={() => void toggleRecording()}
            aria-label={recorder.recording ? 'Остановить и отправить' : 'Записать голосовое'}
          >
            {recorder.recording ? <Square /> : <Mic />}
          </Button>
        )}
        <Button type="submit" size="icon" className="size-10 flex-none" disabled={!idle || recorder.recording} aria-label="Отправить">
          <Send />
        </Button>
      </form>
    </section>
  );
}

