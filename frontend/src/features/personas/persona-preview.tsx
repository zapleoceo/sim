import { AlertCircle, RotateCcw, Send } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { GuardBadge } from '@/components/guard-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { usePreviewChat } from './preview-chat';

/**
 * Trial replies without sending anything to a client; uses the saved persona settings.
 * Errors are shown inline with «Повторить» (the old admin swallowed them).
 * Render with `key={personaId}` so another persona starts a clean chat.
 */
export function PersonaPreview({ personaId }: { personaId: number }) {
  const chat = usePreviewChat(personaId);
  const [text, setText] = useState('');
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages.length, chat.busy, chat.failure]);

  const submit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (chat.send(text)) setText('');
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
                  'max-w-[90%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap [overflow-wrap:anywhere]',
                  m.role === 'user' ? 'bg-bubble-client' : 'bg-bubble-ai',
                  m.failed && 'opacity-70',
                )}
              >
                {m.text}
              </div>
            )}
            <GuardBadge guard={m.guard} />
            {m.meta && <span className="text-[11px] text-muted-foreground">{m.meta}</span>}
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
        {chat.busy && (
          <div className="flex">
            <div className="animate-pulse rounded-2xl bg-bubble-ai px-3 py-2 text-sm text-muted-foreground">печатает…</div>
          </div>
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
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Сообщение"
          aria-label="Сообщение"
          autoComplete="off"
          className="h-10"
        />
        <Button type="submit" size="icon" className="size-10" disabled={chat.busy} aria-label="Отправить">
          <Send />
        </Button>
      </form>
    </section>
  );
}
