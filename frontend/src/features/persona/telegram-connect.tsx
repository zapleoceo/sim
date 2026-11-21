import { useMutation } from '@tanstack/react-query';
import { Link2, Loader2, QrCode } from 'lucide-react';
import { useState } from 'react';
import { InlineError } from '@/components/empty-state';
import { Field } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { channelApi } from './api';
import { LoginShell, QrStep, useChannelLogin, type ConnectProps } from './channel-login';
import { EMPTY_LOGIN_FORM, loginRequest, type LoginForm } from './channel-logic';

/**
 * QR login of a Telegram account: api_id/api_hash (+ proxy) → QR (re-read until it is scanned) →
 * 2FA password if the account has one → connected.
 */
export function TelegramConnect(props: ConnectProps) {
  const login = useChannelLogin(props);
  const { personaId, step, setStep, state, channelId } = login;
  const [form, setForm] = useState<LoginForm>(EMPTY_LOGIN_FORM);
  const [errors, setErrors] = useState<string[]>([]);
  const [password, setPassword] = useState('');

  const start = useMutation({
    mutationFn: () => {
      const { body } = loginRequest(form);
      return channelApi.startTelegram(personaId, body as NonNullable<typeof body>);
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

  return (
    <LoginShell label="Подключение Telegram" step={step} onRetry={() => setStep(null)} onCancel={login.cancel}>
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
            <Button variant="ghost" onClick={props.onClose}>Отмена</Button>
            <Button type="submit" disabled={start.isPending}>
              {start.isPending ? <Loader2 className="animate-spin" /> : <QrCode />}
              Показать QR-код
            </Button>
          </div>
        </form>
      )}

      {state === 'qr' && step && <QrStep step={step} alt="QR-код для входа в Telegram" />}

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
    </LoginShell>
  );
}
