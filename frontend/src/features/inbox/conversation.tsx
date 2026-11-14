import { Link } from '@tanstack/react-router';
import {
  ArrowLeft, Eraser, Info, MessageCircle, Mic, Paperclip, PanelRightClose, PanelRightOpen, PauseCircle, SendHorizontal, Square, User,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Avatar } from '@/components/avatar';
import { ChannelIcon, NeedsOperatorBadge, StageChip, StatusBadge } from '@/components/badges';
import { EmptyState } from '@/components/empty-state';
import { MessageBubble } from '@/components/message-bubble';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { OptionSelect } from '@/components/option-select';
import { STAGES } from '@/lib/stages';
import { canRecord, useVoiceRecorder } from '@/features/personas/use-voice-recorder';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Tip } from '@/components/ui/tooltip';
import type { DialogDetail, Stage } from '@/lib/models';
import { PAUSE_NOTE, groupByDay, isSendKey, pauseNotice } from './inbox-logic';


export function Conversation({
  detail,
  narrow,
  wide,
  cardOpen,
  sending,
  onBack,
  onToggleCard,
  onToggleAi,
  onResolve,
  onSend,
  onSendVoice,
  onSendAttachment,
  onChangeStage,
  onClearChat,
  clearing = false,
}: {
  detail: DialogDetail;
  narrow: boolean;
  wide: boolean;
  cardOpen: boolean;
  sending: boolean;
  onBack: () => void;
  onToggleCard: () => void;
  onToggleAi: (paused: boolean) => void;
  onResolve: () => void;
  /** Resolves true when the message was accepted (the composer is then cleared). */
  onSend: (text: string, requestId: string) => Promise<boolean>;
  onSendVoice?: (audio: Blob, filename: string, requestId: string) => Promise<boolean>;
  onSendAttachment?: (file: File, caption: string, requestId: string) => Promise<boolean>;
  onChangeStage?: (stage: Stage) => Promise<boolean>;
  /** «Очистить чат»: passed only for the owner (the page asks for confirmation first). */
  onClearChat?: () => void;
  clearing?: boolean;
}) {
  const { dialog, client, messages } = detail;
  const [text, setText] = useState('');
  const [sendError, setSendError] = useState('');
  const [stageBusy, setStageBusy] = useState(false);
  const recorder = useVoiceRecorder();
  const fileInput = useRef<HTMLInputElement>(null);
  const textRequest = useRef<{ text: string; id: string } | null>(null);
  const pendingMedia = useRef<{ kind: 'voice' | 'file'; file: Blob | File; name: string; caption: string; id: string } | null>(null);
  const capabilities = dialog.send_capabilities ?? { text: true, voice: false, attachment: false };
  const scroller = useRef<HTMLDivElement>(null);
  const groups = groupByDay(messages);
  const paused = pauseNotice(dialog);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, dialog.id]);

  const send = async (): Promise<void> => {
    const value = text.trim();
    if (!value || sending || !capabilities.text) return;
    const id = textRequest.current?.text === value ? textRequest.current.id : crypto.randomUUID();
    textRequest.current = { text: value, id };
    setSendError('');
    if (await onSend(value, id)) {
      setText('');
      textRequest.current = null;
    } else setSendError('Не удалось подтвердить отправку. Повтор использует тот же запрос.');
  };

  const sendMedia = async (item: NonNullable<typeof pendingMedia.current>): Promise<void> => {
    setSendError('');
    pendingMedia.current = item;
    const accepted = item.kind === 'voice'
      ? await onSendVoice?.(item.file, item.name, item.id)
      : await onSendAttachment?.(item.file as File, item.caption, item.id);
    if (accepted) {
      pendingMedia.current = null;
      if (item.kind === 'file') setText('');
    } else setSendError('Не удалось подтвердить отправку вложения. Проверьте диалог или повторите тот же запрос.');
  };

  const toggleRecording = async (): Promise<void> => {
    if (!recorder.recording) { await recorder.start(); return; }
    const clip = await recorder.stop();
    if (clip) await sendMedia({ kind: 'voice', file: clip.blob, name: clip.filename,
      caption: '', id: crypto.randomUUID() });
  };

  const changeStage = async (stage: Stage): Promise<void> => {
    if (!onChangeStage || stageBusy || stage === client.stage) return;
    setStageBusy(true);
    setSendError('');
    if (!(await onChangeStage(stage))) setSendError('Не удалось изменить этап. Обновите диалог и повторите.');
    setStageBusy(false);
  };

  return (
    <>
      <header className="flex flex-none items-center gap-2 border-b px-3 py-2.5 sm:px-4">
        {narrow && (
          <Button variant="ghost" size="icon-sm" aria-label="Назад к списку" onClick={onBack}>
            <ArrowLeft />
          </Button>
        )}
        <Avatar name={client.display_name} url={client.avatar_url} size={36} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <strong className="truncate text-sm font-semibold">{client.display_name}</strong>
          <span className="flex flex-wrap items-center gap-1.5">
            {dialog.channel_kind && <ChannelIcon kind={dialog.channel_kind} size={18} />}
            <StageChip stage={client.stage} className="h-5 px-2 text-[11px]" />
            {onChangeStage && <OptionSelect<Stage> value={client.stage} onChange={(stage) => void changeStage(stage)}
              options={STAGES.filter((item) => item.aiReachable || item.id === client.stage)
                .map((item) => ({ value: item.id, label: item.label }))} /> }
            <StatusBadge status={client.status} />
            {dialog.needs_operator && <NeedsOperatorBadge />}
          </span>
        </div>
        {dialog.needs_operator && (
          <Tip label="Снять отметку «нужен оператор»">
            <Button variant="outline" size="sm" onClick={onResolve}>
              Обработано
            </Button>
          </Tip>
        )}
        {onClearChat && (
          <Tip label="Удалить историю этого диалога в SIM (в мессенджере она останется)">
            <Button variant="ghost" size="icon-sm" aria-label="Очистить чат" disabled={clearing} onClick={onClearChat}>
              <Eraser />
            </Button>
          </Tip>
        )}
        <Tip label="Когда ИИ выключен, отвечает только оператор">
          <div className="flex items-center gap-2">
            <Switch id="dialog-ai" checked={!dialog.ai_paused} onCheckedChange={(on) => onToggleAi(!on)} />
            <Label htmlFor="dialog-ai">ИИ</Label>
          </div>
        </Tip>
        {wide ? (
          <Button variant="ghost" size="icon-sm" aria-pressed={cardOpen} aria-label="Карточка клиента" onClick={onToggleCard}>
            {cardOpen ? <PanelRightClose /> : <PanelRightOpen />}
          </Button>
        ) : (
          <Button asChild variant="ghost" size="icon-sm">
            <Link to="/clients/$id" params={{ id: String(client.id) }} aria-label="Открыть карточку клиента">
              <User />
            </Link>
          </Button>
        )}
      </header>
      {dialog.needs_operator && <div role="alert" className="border-b bg-warning/15 px-4 py-2 text-sm font-semibold">
        Требуется ответ менеджера. ИИ остановлен; проверьте переписку и ответьте клиенту.
      </div>}
      {paused && (
        <div role="status" aria-label="ИИ выключен"
          className="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b bg-warning/15 px-4 py-2 text-sm">
          <PauseCircle className="size-4 flex-none text-warning" aria-hidden />
          <span className="min-w-0 flex-1">
            <strong className="font-semibold">ИИ выключен.</strong> {paused.text}
          </span>
          <Button size="sm" onClick={() => onToggleAi(false)}>Включить ИИ</Button>
        </div>
      )}
      <div className="flex-none border-b px-4 py-1.5">
        <Link
          to="/clients/$id"
          params={{ id: String(client.id) }}
          hash="conversation"
          className="text-xs text-primary hover:underline"
        >
          Лента клиента — все каналы
        </Link>
      </div>

      <div
        ref={scroller}
        tabIndex={0}
        role="log"
        aria-live="polite"
        aria-label="Сообщения"
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto bg-muted/30 px-3 py-4 scrollbar-thin sm:px-6"
      >
        {groups.length === 0 && <EmptyState icon={MessageCircle} title="Сообщений пока нет" />}
        {groups.map((g) => (
          <div key={g.key} className="flex flex-col gap-2">
            <div className="my-2 flex justify-center">
              <span className="rounded-full bg-background px-3 py-0.5 text-xs text-muted-foreground shadow-xs">{g.label}</span>
            </div>
            {g.items.map((m) => (
              <MessageBubble key={m.msg.id} vm={m} details />
            ))}
          </div>
        ))}
        {paused && paused.waiting > 0 && (
          <div className="my-1 flex justify-center">
            <span role="note" className="rounded-full bg-warning/15 px-3 py-1 text-xs font-medium">
              {PAUSE_NOTE} · без ответа: {paused.waiting}
            </span>
          </div>
        )}
      </div>

      <footer className="flex flex-none flex-col gap-2 border-t bg-background px-3 py-3 sm:px-4">
        {!dialog.ai_paused && (
          <p role="note" className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Info className="size-3.5" />
            Ответ оператора поставит ИИ в этом диалоге на паузу.
          </p>
        )}
        {!capabilities.text && <p className="text-xs text-warning">Канал не подключён: отправка недоступна.</p>}
        {sendError && <p role="alert" className="text-xs text-destructive">{sendError}</p>}
        {recorder.error && <p role="alert" className="text-xs text-destructive">{recorder.error}</p>}
        <div className="flex items-end gap-2">
          <input ref={fileInput} type="file" accept=".png,.jpg,.jpeg,.webp,.pdf,.txt" className="hidden"
            aria-label="Выбрать вложение" onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void sendMedia({ kind: "file", file, name: file.name,
                caption: text.trim(), id: crypto.randomUUID() });
            }} />
          <Button type="button" variant="outline" size="icon" aria-label="Прикрепить файл"
            disabled={sending || !capabilities.attachment} onClick={() => fileInput.current?.click()}><Paperclip /></Button>
          {canRecord() && <Button type="button" variant={recorder.recording ? "destructive" : "outline"} size="icon"
            aria-label={recorder.recording ? "Остановить и отправить голосовое" : "Записать голосовое"}
            disabled={sending || !capabilities.voice} onClick={() => void toggleRecording()}>
            {recorder.recording ? <Square /> : <Mic />}
          </Button>}
          <Textarea
            aria-label="Сообщение клиенту"
            placeholder="Сообщение клиенту"
            rows={1}
            value={text}
            onChange={(e) => { setText(e.target.value); textRequest.current = null; }}
            onKeyDown={(e) => {
              if (isSendKey({ key: e.key, shiftKey: e.shiftKey, isComposing: e.nativeEvent.isComposing })) {
                e.preventDefault();
                void send();
              }
            }}
            className="max-h-40 min-h-9"
          />
          <Button size="icon" aria-label="Отправить" disabled={sending || !text.trim() || !capabilities.text} onClick={() => void send()}>
            <SendHorizontal />
          </Button>
        </div>
        {pendingMedia.current && <Button type="button" variant="outline" size="sm"
          disabled={sending} onClick={() => void sendMedia(pendingMedia.current!)}>Повторить вложение</Button>}
        <p className="hidden text-[11px] text-muted-foreground sm:block">Enter — отправить, Shift+Enter — новая строка</p>
      </footer>
    </>
  );
}
