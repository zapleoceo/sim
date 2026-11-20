import { AlertCircle, FileText, Mic, Paperclip, RotateCcw, Send, Square } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { GuardBadge } from '@/components/guard-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { PreviewAttachment, PreviewStageDiagnostics, PreviewStageOptions, PreviewVoice } from '@/lib/models';
import { cn } from '@/lib/utils';
import { isIdle, PROMPT_ONLY_STAGES, usePreviewChat } from './preview-chat';
import { PreviewGalleryPhoto, PreviewVideoCircle } from './preview-media';
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
  if (v.status === 'pending') {
    return <span role="status" className="text-[11px] text-muted-foreground">Готовим озвучку…</span>;
  }
  if (v.media_url) {
    return <audio controls preload="metadata" src={v.media_url} className="h-9 max-w-full" aria-label="Голосовой ответ" />;
  }
  return <span className="text-[11px] text-warning">Голос не сгенерирован: {v.error}</span>;
}

const STAGE_SWITCHES: ReadonlyArray<readonly [keyof PreviewStageOptions, string]> = [
  ['debounce', 'Рассчитать паузу перед ответом'],
  ['schedule', 'Рассчитать ожидание по расписанию'],
  ['decision_context', 'Учитывать решения прошлых реплик и паузу при передаче оператору'],
];


function StageDiagnostics({ stages }: { stages: PreviewStageDiagnostics }) {
  const schedule = stages.schedule;
  return (
    <details className="max-w-full text-xs text-muted-foreground">
      <summary className="cursor-pointer">Этапы ответа</summary>
      <ul className="mt-1 space-y-0.5">
        <li>Основной промпт: использован</li>
        <li>Пауза перед ответом: {stages.debounce.enabled ? `${stages.debounce.wait_s} с` : 'не рассчитывалась'}</li>
        <li>Ожидание по расписанию: {schedule.enabled ? `${schedule.wait_s} с` : 'не рассчитывалось'}
          {schedule.reason ? ` (${schedule.reason})` : ''}</li>
        <li>Решения прошлых реплик: {stages.decision_context ? 'учитываются' : 'не учитываются'}</li>
        {stages.schedule.enabled || stages.debounce.enabled
          ? <li>Расчёт до начала ответа: {stages.estimated_wait_s} с. В тесте не ждали.</li>
          : null}
      </ul>
    </details>
  );
}

/**
 * Trial replies without sending anything to a client; uses the saved persona settings.
 * Errors are shown inline with «Повторить» (the old admin swallowed them).
 * Render with `key={personaId}` so another persona starts a clean chat.
 */
export function PersonaPreview({
  personaId, asyncEnabled = false, speechJobsReady = false, attachmentQueueEnabled = false, ownerId = 0,
}: { personaId: number; asyncEnabled?: boolean; speechJobsReady?: boolean; attachmentQueueEnabled?: boolean; ownerId?: number }) {
  const modeHelpId = useId();
  const [mode, setMode] = useState<'voice' | 'queued'>('voice');
  const automatic = asyncEnabled && speechJobsReady;
  const queued = automatic || mode === 'queued';
  const modeUnavailable = !automatic && queued && !asyncEnabled;
  const chat = usePreviewChat(personaId, queued, ownerId, attachmentQueueEnabled);
  const recorder = useVoiceRecorder();
  const [sample, setSample] = useState<'photo' | 'circle' | null>(null);
  const [text, setText] = useState('');
  const log = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const idle = isIdle(chat);

  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages.length, chat.busy, chat.uploading, chat.failure, sample]);

  const submit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (!modeUnavailable && chat.send(text)) setText('');
  };

  const attach = (e: ChangeEvent<HTMLInputElement>): void => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || modeUnavailable) return;
    void chat.sendFile(file, file.name, { caption: text }).then((sent) => {
      if (sent) setText('');
    });
  };

  const toggleRecording = async (): Promise<void> => {
    if (!recorder.recording) {
      if (modeUnavailable) return;
      await recorder.start();
      return;
    }
    const clip = await recorder.stop();
    if (clip && !modeUnavailable) void chat.sendFile(clip.blob, clip.filename, { voice: true });
  };

  return (
    <section aria-labelledby="preview-h" className="flex min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="preview-h" className="text-base font-semibold">
          Проверить персонажа
        </h2>
        <Button variant="ghost" size="sm" onClick={() => { chat.reset(); setSample(null); }} disabled={!idle || recorder.recording || chat.awaitingOperation}>
          Очистить
        </Button>
      </div>
      {!automatic && (asyncEnabled || queued) && (
        <div className="space-y-2 text-sm">
          <label className="flex flex-wrap items-center justify-between gap-2">
            Режим предпросмотра
            <select
              aria-describedby={modeUnavailable ? modeHelpId : undefined}
              className="max-w-full rounded-md border bg-background px-2 py-1"
              value={mode}
              onChange={(e) => setMode(e.target.value === 'queued' ? 'queued' : 'voice')}
              disabled={!idle || recorder.recording || chat.awaitingOperation || !!chat.failure}
            >
              <option value="voice">Обычный, с настройками озвучки</option>
              <option value="queued" disabled={!asyncEnabled}>Через очередь, только текст</option>
            </select>
          </label>
          {modeUnavailable && (
            <p id={modeHelpId} role="status" className="text-muted-foreground">
              Очередь отключена. Если переключатель недоступен, дождитесь статуса запроса или очистите чат. Затем выберите обычный режим.
            </p>
          )}
        </div>
      )}
      <fieldset disabled={!idle || recorder.recording} className="space-y-2 rounded-lg border p-3 text-sm">
        <legend className="px-1 font-medium">Этапы тестового ответа</legend>
        <p className="text-xs text-muted-foreground">
          {!automatic && chat.queued ? 'Тестовые ответы через очередь пока только текстовые.' : 'Только этот чат. Расчёты не задерживают ответ; ожидание самой модели остаётся.'}
          Отдельные агенты планирования здесь не запускаются.
        </p>
        {STAGE_SWITCHES.map(([key, label]) => (
          <label key={key} className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={chat.stages[key]}
              onChange={(e) => chat.setStages({ ...chat.stages, [key]: e.target.checked })}
            />
            {label}
          </label>
        ))}
        <Button
          type="button" variant="outline" size="sm"
          onClick={() => chat.setStages(PROMPT_ONLY_STAGES)}
        >
          Основной промпт без контекста решений
        </Button>
        <p className="text-xs text-muted-foreground">
          История, факты о персонаже (включая распорядок), формат и контроль ответа остаются.
          Сохранённые настройки и живые диалоги не меняются.
        </p>
      </fieldset>

      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => setSample('photo')}>
          Показать фото из галереи
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={() => setSample('circle')}>
          Показать тестовый кружок
        </Button>
      </div>

      <div
        ref={log}
        role="log"
        aria-live="polite"
        className="flex max-h-[28rem] min-h-52 flex-col gap-2 overflow-y-auto rounded-lg bg-muted/60 p-3"
      >
        {chat.messages.length === 0 && !chat.busy && !sample && (
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
            {m.stages && <StageDiagnostics stages={m.stages} />}
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
        {sample && (
          <div className="flex max-w-full flex-col items-start gap-1 self-start rounded-2xl bg-bubble-ai px-3 py-2 text-sm">
            <span className="text-xs text-muted-foreground">Предпросмотр медиа</span>
            {sample === 'photo' ? <PreviewGalleryPhoto personaId={personaId} /> : <PreviewVideoCircle />}
          </div>
        )}
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
            {!chat.failure.terminal && (
              <Button variant="outline" size="sm" onClick={chat.retry}>
                <RotateCcw />
                {chat.queued ? 'Проверить запрос' : 'Повторить'}
              </Button>
            )}
          </div>
        )}
      </div>

      {chat.attachmentQueue.waiting && <p role="status" className="text-sm">Готовим вложение…</p>}
      {chat.attachmentQueue.error && (
        <div className="space-y-1 text-sm">
          <p role="alert">{chat.attachmentQueue.error}</p>
          {chat.attachmentQueue.pending && <Button type="button" variant="outline"
            onClick={chat.attachmentQueue.retry}>Проверить вложение</Button>}
        </div>
      )}
      {chat.attachmentQueue.ready && (
        <div className="rounded-lg border p-2 text-sm">
          <AttachmentView a={chat.attachmentQueue.ready} />
          <p>{chat.attachmentQueue.ready.text}</p>
          <Button type="button" variant="ghost" onClick={chat.attachmentQueue.clear}>Убрать вложение</Button>
        </div>
      )}

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
          disabled={!idle || recorder.recording || chat.awaitingOperation || modeUnavailable || chat.attachmentQueue.pending}
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
            disabled={!idle || chat.awaitingOperation || (modeUnavailable && !recorder.recording) || chat.attachmentQueue.pending}
            onClick={() => void toggleRecording()}
            aria-label={recorder.recording ? 'Остановить и отправить' : 'Записать голосовое'}
          >
            {recorder.recording ? <Square /> : <Mic />}
          </Button>
        )}
        <Button type="submit" size="icon" className="size-10 flex-none" disabled={!idle || recorder.recording || chat.awaitingOperation || modeUnavailable || (chat.attachmentQueue.pending && !chat.attachmentQueue.ready)} aria-label="Отправить">
          <Send />
        </Button>
      </form>
    </section>
  );
}
