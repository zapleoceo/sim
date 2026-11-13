import { CheckCircle2, Loader2, TriangleAlert } from 'lucide-react';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { ChannelIcon } from '@/components/badges';
import { InlineError } from '@/components/empty-state';
import { Field } from '@/components/field';
import { OptionSelect } from '@/components/option-select';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { channelsApi } from '@/lib/api/channels';
import { humanizeChannelError } from '@/lib/channel-error';
import type { Channel, ChannelKind, ConnectState, Persona } from '@/lib/models';
import { CHANNEL_KINDS, CHANNEL_LABELS } from '@/lib/stages';
import { cn } from '@/lib/utils';
import {
  EMPTY_CONNECT, QR_POLL_MS, connectBody, nextStep, pollingDone, prefillConnect, sameValues, validateConnect, verifyValue,
  type ConnectErrors, type ConnectValues, type WizardStep,
} from './channel-wizard-logic';

function Warn({ children }: { children: ReactNode }) {
  return (
    <p className="flex gap-2 rounded-lg bg-warning/12 p-3 text-sm text-foreground">
      <TriangleAlert className="mt-0.5 size-4 flex-none text-warning" />
      <span>{children}</span>
    </p>
  );
}

function HowTo({ children }: { children: ReactNode }) {
  return <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground [&_code]:rounded [&_code]:bg-muted [&_code]:px-1">{children}</ol>;
}

/**
 * Add / connect a channel: setup → credentials → (QR | code | 2FA password) → done.
 * `onClose(changed)` — changed is true when a channel was created or connected.
 */
export function ChannelWizard({
  personas,
  channel: existing,
  onClose,
}: {
  personas: Persona[];
  channel?: Channel;
  onClose: (changed: boolean) => void;
}) {
  const isNew = !existing;
  const [step, setStep] = useState<WizardStep>(existing ? 'connect' : 'setup');
  const [channel, setChannel] = useState<Channel | null>(existing ?? null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [need, setNeed] = useState<ConnectState['need'] | null>(null);
  const [qr, setQr] = useState<string | null>(null);

  const [kindChoice, setKindChoice] = useState<ChannelKind>('telegram');
  const [name, setName] = useState(CHANNEL_LABELS.telegram);
  const [personaId, setPersonaId] = useState<number | null>(personas.length === 1 ? (personas[0]?.id ?? null) : null);
  const [setupTried, setSetupTried] = useState(false);

  const [creds, setCreds] = useState<ConnectValues>(EMPTY_CONNECT);
  // A reconnect starts with the credentials saved at the last connect (encrypted on the server).
  useEffect(() => {
    if (!existing?.has_saved_credentials) return;
    let live = true;
    channelsApi
      .credentials(existing.id)
      .then((saved) => {
        if (live) setCreds((c) => (sameValues(c, EMPTY_CONNECT) ? prefillConnect(saved) : c));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [existing]);
  const [credErrors, setCredErrors] = useState<ConnectErrors>({});
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState('');

  const kind = channel?.kind ?? kindChoice;
  const setCred = (key: keyof ConnectValues) => (e: { target: { value: string } }) =>
    setCreds((c) => ({ ...c, [key]: e.target.value }));

  const apply = (s: ConnectState, current: WizardStep): void => {
    setMessage(humanizeChannelError(s.message));
    setNeed(s.need ?? null);
    if (s.qr_data_uri) setQr(s.qr_data_uri);
    setStep(nextStep(s, kind, current));
  };

  const run = async <T,>(work: () => Promise<T>, next: (v: T) => void): Promise<void> => {
    setBusy(true);
    try {
      next(await work());
    } catch {
      /* the HTTP layer already showed a toast */
    } finally {
      setBusy(false);
    }
  };

  // WhatsApp QR: refresh the state every 3 s until the channel is active or failed.
  useEffect(() => {
    if (step !== 'qr' || !channel) return;
    let stopped = false;
    const timer = window.setInterval(() => {
      channelsApi
        .state(channel.id)
        .then((s) => {
          if (stopped) return;
          setMessage(humanizeChannelError(s.message));
          if (s.status === 'active') setStep('done');
          else if (pollingDone(s)) setStep('connect');
          else if (s.need) setStep(nextStep(s, channel.kind, 'qr'));     // QR scanned, 2FA password next
          else if (s.qr_data_uri) setQr(s.qr_data_uri);
        })
        .catch(() => undefined);
    }, QR_POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [step, channel]);

  const connect = (ch: Channel | null = channel): void => {
    if (!ch) return;
    if (ch.kind !== 'whatsapp') {
      const errors = validateConnect(ch.kind, creds, Boolean(ch.has_saved_credentials));
      setCredErrors(errors);
      if (Object.keys(errors).length) return;
    }
    void run(() => channelsApi.connect(ch.id, connectBody(ch.kind, creds)), (s) => apply(s, 'connect'));
  };

  const create = (e?: FormEvent): void => {
    e?.preventDefault();
    setSetupTried(true);
    if (!name.trim() || name.length > 80 || personaId === null) return;
    void run(
      () => channelsApi.create({ kind: kindChoice, persona_id: personaId, name: name.trim() }),
      (ch) => {
        setChannel(ch);
        setStep('connect');
        // WhatsApp needs no input: ask for the QR right away.
        if (ch.kind === 'whatsapp') connect(ch);
      },
    );
  };

  const submitConnect = (e?: FormEvent): void => {
    e?.preventDefault();
    connect();
  };

  const verify = (e?: FormEvent): void => {
    e?.preventDefault();
    if (!channel) return;
    const value = verifyValue(step, code);
    if (!value) {
      setCodeError(step === 'password' ? 'Введите пароль' : 'Введите код (не короче 4 символов)');
      return;
    }
    setCodeError('');
    const current = step;
    void run(() => channelsApi.verify(channel.id, value), (s) => {
      setCode('');
      apply(s, current);
    });
  };

  const pickKind = (k: ChannelKind): void => {
    // Keep a custom name; replace only the auto-filled one.
    if (!name.trim() || name === CHANNEL_LABELS[kindChoice]) setName(CHANNEL_LABELS[k]);
    setKindChoice(k);
  };

  const close = (): void => onClose(step === 'done' || channel !== null);
  const msg = message && <InlineError>{message}</InlineError>;

  return (
    <Dialog open onOpenChange={(open) => !open && close()}>
      <DialogContent onInteractOutside={(e) => e.preventDefault()} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isNew ? 'Новый канал' : 'Подключение канала'}</DialogTitle>
          <DialogDescription>
            {channel ? `${CHANNEL_LABELS[kind]} · ${channel.name}` : 'Выберите тип, название и персонажа'}
          </DialogDescription>
        </DialogHeader>

        {step === 'setup' && (
          <form id="wizard-form" onSubmit={create} noValidate className="flex flex-col gap-4">
            <div role="radiogroup" aria-label="Тип канала" className="grid grid-cols-2 gap-2">
              {CHANNEL_KINDS.map((k) => (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={kindChoice === k}
                  onClick={() => pickKind(k)}
                  className={cn(
                    'flex items-center gap-2 rounded-lg border p-3 text-left text-sm transition-colors hover:bg-accent',
                    kindChoice === k && 'border-primary bg-accent font-medium',
                  )}
                >
                  <ChannelIcon kind={k} />
                  {CHANNEL_LABELS[k]}
                </button>
              ))}
            </div>
            <Field label="Название" error={setupTried && !name.trim() ? 'Укажите название' : name.length > 80 ? 'Не длиннее 80 символов' : undefined}>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Персонаж" error={setupTried && personaId === null ? 'Выберите персонажа' : undefined}>
              <OptionSelect
                value={personaId}
                onChange={setPersonaId}
                placeholder="Выберите персонажа"
                options={personas.map((p) => ({ value: p.id as number | null, label: p.name }))}
              />
            </Field>
          </form>
        )}

        {step === 'connect' && channel?.has_saved_credentials && (
          <p className="rounded-lg bg-muted/60 px-3 py-2 text-sm" role="status">
            Данные прошлого подключения сохранены (зашифрованы) и подставлены в форму. Проверьте их, особенно номер, и нажмите «Подключить».
          </p>
        )}
        {step === 'connect' && kind === 'telegram' && (
          <form id="wizard-form" onSubmit={submitConnect} noValidate className="flex flex-col gap-4">
            <HowTo>
              <li>Откройте в Telegram бота <strong>@BotFather</strong>.</li>
              <li>Отправьте команду <code>/newbot</code> и следуйте подсказкам: имя и username бота.</li>
              <li>BotFather пришлёт токен вида <code>123456:ABC-…</code>. Скопируйте его сюда.</li>
            </HowTo>
            <Field label="Токен бота" error={credErrors.bot_token}>
              <Input value={creds.bot_token} onChange={setCred('bot_token')} autoComplete="off" spellCheck={false} />
            </Field>
            {msg}
          </form>
        )}

        {step === 'connect' && kind === 'telegram_user' && (
          <form id="wizard-form" onSubmit={submitConnect} noValidate className="flex flex-col gap-4">
            <Warn>
              Подключается личный аккаунт Telegram (не бот). При подозрительной активности Telegram может ограничить аккаунт.
              Лучше выделенный номер и аккаунт.
            </Warn>
            <HowTo>
              <li>Откройте <strong>my.telegram.org</strong>, войдите по номеру и выберите <strong>API development tools</strong>.</li>
              <li>Создайте приложение (любое название) и скопируйте <code>api_id</code> и <code>api_hash</code> сюда.</li>
              <li>
                {creds.method === 'qr'
                  ? 'Затем отсканируйте QR-код в Telegram на телефоне: Настройки → Устройства → Подключить устройство.'
                  : 'Telegram пришлёт код входа в приложение (чат «Telegram») — он понадобится на следующем шаге.'}
              </li>
            </HowTo>
            <div role="radiogroup" aria-label="Как войти" className="grid grid-cols-2 gap-2 text-sm">
              {([['qr', 'По QR-коду', 'без кода, рекомендуется'], ['code', 'По коду', 'код придёт в Telegram']] as const).map(([value, label, hint]) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={creds.method === value}
                  onClick={() => setCreds((c) => ({ ...c, method: value }))}
                  className={cn(
                    'flex flex-col items-start rounded-lg border p-2.5 text-left',
                    creds.method === value ? 'border-primary bg-primary/5' : 'hover:bg-muted',
                  )}
                >
                  <span className="font-medium">{label}</span>
                  <span className="text-xs text-muted-foreground">{hint}</span>
                </button>
              ))}
            </div>
            <Field label="api_id" error={credErrors.api_id}>
              <Input value={creds.api_id} onChange={setCred('api_id')} inputMode="numeric" autoComplete="off" />
            </Field>
            <Field label="api_hash" error={credErrors.api_hash}>
              <Input value={creds.api_hash} onChange={setCred('api_hash')} autoComplete="off" spellCheck={false} />
            </Field>
            {creds.method !== 'qr' && (
              <Field label="Телефон аккаунта" error={credErrors.phone}>
                <Input type="tel" value={creds.phone} onChange={setCred('phone')} placeholder="+380501234567" autoComplete="off" />
              </Field>
            )}
            <Field label="Прокси (необязательно)" error={credErrors.proxy}>
              <Input value={creds.proxy} onChange={setCred('proxy')} placeholder="socks5://user:pass@host:port" autoComplete="off" />
            </Field>
            {msg}
          </form>
        )}

        {step === 'connect' && kind === 'whatsapp' && (
          <div className="flex flex-col gap-3 text-sm">
            <p>Сейчас появится QR-код. Откройте WhatsApp на телефоне → Связанные устройства → Привязка устройства и наведите камеру.</p>
            <Warn>Используется неофициальный клиент: у номера есть риск блокировки. Лучше выделенный номер.</Warn>
            {msg}
          </div>
        )}

        {step === 'connect' && kind === 'instagram' && (
          <form id="wizard-form" onSubmit={submitConnect} noValidate className="flex flex-col gap-4">
            <Warn>
              Вход идёт через неофициальный API Instagram. Есть риск проверок безопасности и блокировки аккаунта. Используйте
              отдельный аккаунт и прокси из той же страны, что и обычный вход.
            </Warn>
            <Field label="Логин Instagram" error={credErrors.username}>
              <Input value={creds.username} onChange={setCred('username')} autoComplete="off" />
            </Field>
            <Field label="Пароль" error={credErrors.password}>
              <Input type="password" value={creds.password} onChange={setCred('password')} autoComplete="new-password" />
            </Field>
            <Field label="Прокси (необязательно)" error={credErrors.proxy}>
              <Input value={creds.proxy} onChange={setCred('proxy')} placeholder="http://user:pass@host:port" autoComplete="off" />
            </Field>
            {msg}
          </form>
        )}

        {step === 'qr' && (
          <div className="flex flex-col items-center gap-3 text-center text-sm">
            {qr ? (
              <img src={qr} alt={`QR-код для привязки ${CHANNEL_LABELS[kind]}`} width={260} height={260} className="rounded-lg bg-white p-2" />
            ) : (
              <div className="size-[260px] animate-pulse rounded-lg bg-muted" aria-hidden="true" />
            )}
            <p className="flex items-center gap-2 text-muted-foreground" role="status">
              <Loader2 className="size-4 animate-spin" />
              Ждём, пока вы отсканируете код…
            </p>
            {msg}
          </div>
        )}

        {(step === 'code' || step === 'password') && (
          <form id="wizard-form" onSubmit={verify} noValidate className="flex flex-col gap-4 text-sm">
            <p>
              {step === 'password'
                ? 'На аккаунте включена двухфакторная защита. Введите облачный пароль Telegram.'
                : kind === 'telegram_user'
                  ? 'Telegram отправил код входа в приложение на другом устройстве или по SMS.'
                  : need === '2fa'
                    ? 'Instagram запросил код двухфакторной аутентификации.'
                    : 'Instagram запросил подтверждение входа. Код отправлен на почту или телефон аккаунта.'}
            </p>
            <Field label={step === 'password' ? 'Пароль 2FA' : 'Код'} error={codeError}>
              {step === 'password' ? (
                <Input type="password" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" />
              ) : (
                <Input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" />
              )}
            </Field>
            {msg}
          </form>
        )}

        {step === 'done' && (
          <div className="flex flex-col items-center gap-2 py-4 text-center">
            <CheckCircle2 className="size-10 text-success" />
            <h3 className="font-semibold">Канал подключён</h3>
            <p className="text-sm text-muted-foreground">Входящие сообщения начнут появляться в диалогах.</p>
          </div>
        )}

        <DialogFooter>
          {step === 'done' ? (
            <Button onClick={() => onClose(true)}>Готово</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={close}>
                {channel ? 'Закрыть' : 'Отмена'}
              </Button>
              {step === 'setup' && (
                <Button disabled={busy} onClick={() => create()}>
                  Далее
                </Button>
              )}
              {step === 'connect' &&
                (kind === 'whatsapp' ? (
                  <Button disabled={busy} onClick={() => connect()}>
                    Показать QR
                  </Button>
                ) : (
                  <Button disabled={busy} onClick={() => submitConnect()}>
                    Подключить
                  </Button>
                ))}
              {(step === 'code' || step === 'password') && (
                <Button disabled={busy} onClick={() => verify()}>
                  Подтвердить
                </Button>
              )}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
