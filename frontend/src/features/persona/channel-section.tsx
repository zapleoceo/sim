import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link2, Loader2, PowerOff, QrCode, Radio, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState, InlineError } from '@/components/empty-state';
import { Field, Section } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query';
import type { Channel, LoginResponse } from '@/lib/types';
import { channelApi } from './api';
import { EMPTY_LOGIN_FORM, channelStatus, loginRequest, shouldPoll, type LoginForm } from './channel-logic';
import { usePersonaId } from './persona-scope';

export const LOGIN_POLL_MS = 2_000;

function ChannelRow({ channel }: { channel: Channel }) {
  const personaId = usePersonaId();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const status = channelStatus(channel.status);
  const disable = useMutation({
    mutationFn: () => channelApi.disable(personaId, channel.id),
    onSuccess: () => {
      toast.success('Канал отключён — аккаунт выйдет из сети в течение минуты');
      void queryClient.invalidateQueries({ queryKey: qk.channels(personaId) });
      void queryClient.invalidateQueries({ queryKey: qk.personaList });
    },
  });
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{channel.title || 'Telegram'}</span>
          {channel.account_label && <span className="text-sm text-muted-foreground">{channel.account_label}</span>}
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
        {channel.last_activity_at && (
          <span className="text-xs text-muted-foreground">Последняя активность: {formatDateTime(channel.last_activity_at)}</span>
        )}
        {channel.last_error && <span className="text-xs break-words text-destructive">{channel.last_error}</span>}
      </div>
      {channel.status !== 'disabled' && (
        <Button
          variant="ghost-destructive"
          size="sm"
          disabled={disable.isPending}
          onClick={async () => {
            const ok = await confirm({
              title: 'Отключить канал?',
              message: 'Персонаж перестанет получать и отправлять сообщения через этот аккаунт. Вернуть — подключить заново.',
              confirmLabel: 'Отключить',
              danger: true,
            });
            if (ok) disable.mutate();
          }}
        >
          <PowerOff />
          Отключить
        </Button>
      )}
    </li>
  );
}

/**
 * QR login of a Telegram account: api_id/api_hash (+ proxy) → QR (re-read every `pollMs` until it
 * is scanned) → 2FA password if the account has one → connected. Secrets are only sent, never shown.
 */
export function TelegramConnect({ onClose, pollMs = LOGIN_POLL_MS }: { onClose: () => void; pollMs?: number }) {
  const personaId = usePersonaId();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<LoginForm>(EMPTY_LOGIN_FORM);
  const [errors, setErrors] = useState<string[]>([]);
  const [step, setStep] = useState<LoginResponse | null>(null);
  const [password, setPassword] = useState('');
  const reported = useRef(false);
  const state = step?.login.state;
  const channelId = step?.channel.id;

  const start = useMutation({
    mutationFn: () => {
      const { body } = loginRequest(form);
      return channelApi.startLogin(personaId, body as NonNullable<typeof body>);
    },
    onSuccess: setStep,
  });
  const sendPassword = useMutation({
    mutationFn: () => channelApi.password(personaId, channelId as number, password),
    onSuccess: (next) => {
      setPassword('');
      setStep(next);
    },
  });

  // Re-read the QR (a fresh one every ~30 s) until it is scanned.
  useEffect(() => {
    if (!shouldPoll(state) || channelId === undefined) return undefined;
    let alive = true;
    const timer = window.setTimeout(() => {
      channelApi
        .loginState(personaId, channelId)
        .then((next) => {
          if (alive) setStep(next);
        })
        .catch((error: unknown) => {
          if (alive) setStep((prev) => prev && { ...prev, login: { state: 'error', message: errorMessage(error), qr: null } });
        });
    }, pollMs);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [state, step, channelId, personaId, pollMs]);

  useEffect(() => {
    if (state !== 'connected' || reported.current) return;
    reported.current = true;
    toast.success(`Аккаунт ${step?.channel.account_label ?? ''} подключён. Персонаж выйдет в сеть в течение минуты`);
    void queryClient.invalidateQueries({ queryKey: qk.channels(personaId) });
    void queryClient.invalidateQueries({ queryKey: qk.personaList });
    onClose();
  }, [state, step, queryClient, personaId, onClose]);

  const cancel = (): void => {
    if (channelId !== undefined && (state === 'qr' || state === 'password')) {
      void channelApi.cancel(personaId, channelId).catch(() => undefined);
    }
    onClose();
  };

  return (
    <div className="flex flex-col gap-4 rounded-lg border p-3 sm:p-4" role="region" aria-label="Подключение Telegram">
      {step === null && (
        <form
          noValidate
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            const checked = loginRequest(form);
            setErrors(checked.errors);
            if (checked.body) start.mutate();
          }}
        >
          <p className="text-sm text-muted-foreground">
            api_id и api_hash — на my.telegram.org → API development tools. Потом отсканируете QR-код в приложении Telegram.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <Field id="tg-api-id" label="api_id">
              <Input id="tg-api-id" inputMode="numeric" autoComplete="off" value={form.api_id} onChange={(e) => setForm({ ...form, api_id: e.target.value })} />
            </Field>
            <Field id="tg-api-hash" label="api_hash">
              <Input id="tg-api-hash" type="password" autoComplete="off" value={form.api_hash} onChange={(e) => setForm({ ...form, api_hash: e.target.value })} />
            </Field>
            <Field id="tg-proxy" label="Прокси (необязательно)" hint="socks5://user:pass@host:port">
              <Input id="tg-proxy" autoComplete="off" value={form.proxy} onChange={(e) => setForm({ ...form, proxy: e.target.value })} />
            </Field>
          </div>
          {errors.length > 0 && <InlineError>{errors.join('. ')}</InlineError>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Отмена</Button>
            <Button type="submit" disabled={start.isPending}>
              {start.isPending ? <Loader2 className="animate-spin" /> : <QrCode />}
              Показать QR-код
            </Button>
          </div>
        </form>
      )}

      {state === 'qr' && step && (
        <div className="flex flex-col items-center gap-3 text-center">
          {step.login.qr && (
            <img src={step.login.qr} alt="QR-код для входа в Telegram" className="size-56 rounded-md bg-white p-2 sm:size-64" />
          )}
          <p className="max-w-md text-sm text-muted-foreground">{step.login.message}</p>
          <p className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
            <Loader2 className="size-3 animate-spin" />
            Жду, когда отсканируете…
          </p>
        </div>
      )}

      {state === 'password' && (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (password) sendPassword.mutate();
          }}
        >
          <Field id="tg-password" label="Пароль двухфакторной защиты" hint={step?.login.message}>
            <Input id="tg-password" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" disabled={!password || sendPassword.isPending}>
              {sendPassword.isPending ? <Loader2 className="animate-spin" /> : <Link2 />}
              Войти
            </Button>
          </div>
        </form>
      )}

      {(state === 'error' || state === 'none') && step && (
        <div className="flex flex-col gap-3">
          <InlineError>{step.login.message}</InlineError>
          <div className="flex justify-end">
            <Button variant="outline" onClick={() => setStep(null)}>Попробовать снова</Button>
          </div>
        </div>
      )}

      {step !== null && state !== 'connected' && (
        <div className="flex justify-end border-t pt-3">
          <Button variant="ghost" size="sm" onClick={cancel}>
            <X />
            Отменить подключение
          </Button>
        </div>
      )}
    </div>
  );
}

/** «Канал»: the persona's messenger accounts, connecting one by QR, switching one off. */
export function ChannelSection({ pollMs }: { pollMs?: number }) {
  const personaId = usePersonaId();
  const channels = useQuery({ queryKey: qk.channels(personaId), queryFn: () => channelApi.list(personaId) });
  const [connecting, setConnecting] = useState(false);
  const items = channels.data?.items ?? [];
  const hasTelegram = items.some((c) => c.kind === 'telegram_user');
  return (
    <Section
      id="channel"
      title="Канал"
      description="Аккаунт Telegram, через который говорит персонаж. Клиенты, написавшие на этот аккаунт, — её лиды."
      actions={
        !connecting && (
          <Button variant="outline" size="sm" onClick={() => setConnecting(true)}>
            <QrCode />
            {hasTelegram ? 'Подключить заново' : 'Подключить Telegram'}
          </Button>
        )
      }
    >
      {channels.isPending && <Skeleton className="h-12 w-full" />}
      {channels.isError && <ErrorState onRetry={() => void channels.refetch()} />}
      {channels.isSuccess && items.length === 0 && !connecting && (
        <EmptyState icon={Radio} title="Канал не подключён" hint="Без канала персонаж никому не отвечает." className="py-4" />
      )}
      {items.length > 0 && (
        <ul className="flex flex-col divide-y">
          {items.map((c) => <ChannelRow key={c.id} channel={c} />)}
        </ul>
      )}
      {connecting && <TelegramConnect onClose={() => setConnecting(false)} pollMs={pollMs} />}
    </Section>
  );
}
