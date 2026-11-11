import { useQuery } from '@tanstack/react-query';
import { Send } from 'lucide-react';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { ErrorState, InlineError } from '@/components/empty-state';
import { Field } from '@/components/field';
import { OptionSelect } from '@/components/option-select';
import { Page } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { studioApi, type StudioMessage } from '@/lib/api/studio';
import { errorMessage } from '@/lib/errors';
import { qk } from '@/lib/query';
import { cn } from '@/lib/utils';

export const STUDIO_MAX_MESSAGES = 24;
export const STUDIO_MAX_CHARS = 32000;
export const STUDIO_LIMIT_ERROR =
  'Достигнут лимит разговора: 24 сообщения или 32 000 символов. Сохраните нужную переписку и нажмите «Новый разговор». Черновик сохранится.';

/** The conversation must stay within 24 messages and 32 000 characters (code points). */
export function withinStudioLimit(messages: readonly StudioMessage[]): boolean {
  const chars = messages.reduce((n, m) => n + [...m.content].length, 0);
  return messages.length <= STUDIO_MAX_MESSAGES && chars <= STUDIO_MAX_CHARS;
}

function MediaPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-xl border bg-card p-4 shadow-xs sm:p-5">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Placeholder({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-56 place-items-center rounded-lg bg-muted px-5 text-center text-sm leading-relaxed text-muted-foreground">
      {children}
    </div>
  );
}

/** Owner's private studio: source photo, video from it, and a chat on subscription models. */
export function StudioPage() {
  const studio = useQuery({ queryKey: qk.studio, queryFn: studioApi.get, retry: false });
  const [model, setModel] = useState('');
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<StudioMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');

  const state = studio.data;
  useEffect(() => {
    if (state && !state.models.includes(model)) setModel(state.models[0] ?? '');
  }, [state, model]);

  const canType = !!state?.text_available && !sending;

  const send = async (e: FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault();
    const content = draft.trim();
    if (!content || !state?.text_available || !state.models.includes(model) || sending) return;
    const pending: StudioMessage[] = [...messages, { role: 'user', content }];
    if (!withinStudioLimit(pending)) {
      setError(STUDIO_LIMIT_ERROR);
      return;
    }
    setSending(true);
    setError('');
    try {
      const result = await studioApi.chat(model, pending);
      setMessages([...pending, { role: 'assistant', content: result.text }]);
      setDraft('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const clear = (): void => {
    if (sending) return;
    setMessages([]);
    setError('');
  };

  return (
    <Page>
      <header className="flex flex-col gap-2">
        <p className="text-xs font-medium tracking-[0.12em] text-muted-foreground uppercase">SIM / личная студия</p>
        <h1 className="text-2xl font-semibold sm:text-3xl">Фото, видео и разговор</h1>
        <p className="text-sm text-muted-foreground">
          Закрытый интерфейс владельца. Подписочные модели не используются для ответов внешним клиентам.
        </p>
      </header>

      {studio.isPending && (
        <div role="status" aria-label="Проверяем доступность студии…" className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-72 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      )}
      {studio.isError && (
        <ErrorState title={errorMessage(studio.error)} onRetry={() => void studio.refetch()} />
      )}

      {state && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <MediaPanel title="Исходное фото">
              {state.photo_url ? (
                <img
                  src={state.photo_url}
                  alt="Исходное фото персонажа, предоставленное владельцем"
                  className="max-h-[28rem] w-full rounded-lg object-contain"
                />
              ) : (
                <Placeholder>Исходное фото пока не подключено. Случайное изображение вместо него не используется.</Placeholder>
              )}
            </MediaPanel>
            <MediaPanel title="Видео с этого фото">
              {state.video_url ? (
                <video
                  src={state.video_url}
                  controls
                  playsInline
                  preload="metadata"
                  aria-label="Видео персонажа"
                  className="max-h-[28rem] w-full rounded-lg"
                />
              ) : (
                <Placeholder>
                  Видео ещё не сгенерировано. Здесь появится подтверждённый результат, а не демонстрационная подмена.
                </Placeholder>
              )}
            </MediaPanel>
          </div>
          {state.detail && <p className="rounded-lg bg-muted px-4 py-3 text-sm leading-relaxed">{state.detail}</p>}

          <section aria-labelledby="chat-h" className="flex flex-col gap-4 rounded-xl border bg-card p-4 shadow-xs sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="chat-h" className="text-base font-semibold">
                Пообщаться
              </h2>
              <Button variant="outline" size="sm" onClick={clear} disabled={sending || !messages.length}>
                Новый разговор
              </Button>
            </div>
            <div className="flex flex-col gap-1 text-xs text-muted-foreground">
              <p>Кнопка очистит текущую переписку. Черновик останется.</p>
              <p>Переписка хранится только до закрытия страницы. Фото и видео автоматически модели не передаются.</p>
            </div>

            <Field label="Подписочная модель" className="sm:max-w-sm">
              <OptionSelect
                value={model}
                onChange={setModel}
                options={state.models.map((m) => ({ value: m, label: m }))}
                disabled={!canType}
              />
            </Field>
            {!state.text_available && (
              <p className="rounded-lg bg-muted px-4 py-3 text-sm">
                Текстовая модель пока недоступна. Платная замена автоматически не включается.
              </p>
            )}

            <div role="log" aria-live="polite" className="flex max-h-[32rem] flex-col gap-3 overflow-y-auto">
              {messages.length === 0 ? (
                <p className="text-sm text-muted-foreground">Начните разговор, когда подписочная модель станет доступна.</p>
              ) : (
                messages.map((m, i) => (
                  <article
                    key={i}
                    className={cn(
                      'flex flex-col gap-1 rounded-xl px-4 py-3',
                      m.role === 'user' ? 'ml-[10%] bg-bubble-client' : 'mr-[10%] bg-bubble-ai',
                    )}
                  >
                    <strong className="text-xs">{m.role === 'user' ? 'Вы' : 'Персонаж'}</strong>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">{m.content}</p>
                  </article>
                ))
              )}
            </div>

            {error && <InlineError>{error}</InlineError>}

            <form onSubmit={(e) => void send(e)} className="flex flex-col gap-2">
              <Field label="Сообщение">
                <Textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  maxLength={8000}
                  disabled={!canType}
                  placeholder="Напишите сообщение…"
                  className="min-h-20 max-h-64"
                />
              </Field>
              <Button type="submit" className="self-end" disabled={!canType || !model || !draft.trim()}>
                <Send />
                {sending ? 'Ожидаем ответ…' : 'Отправить'}
              </Button>
            </form>
          </section>
        </>
      )}
    </Page>
  );
}
