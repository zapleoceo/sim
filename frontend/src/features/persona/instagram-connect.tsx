import { useMutation } from '@tanstack/react-query';
import { Link2, Loader2, LogIn, RotateCw } from 'lucide-react';
import { useState } from 'react';
import { InlineError } from '@/components/empty-state';
import { Field } from '@/components/field';
import { Segmented } from '@/components/segmented';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { channelApi } from './api';
import { LoginShell, useChannelLogin, type ConnectProps } from './channel-login';
import {
  EMPTY_INSTAGRAM_FORM, PROXY_HINT, codeLabel, instagramRequest, type InstagramForm, type InstagramMethod,
} from './channel-logic';

const METHODS = [
  { value: 'password', label: 'Логин и пароль' },
  { value: 'sessionid', label: 'Вход по sessionid', title: 'Cookie «sessionid» из браузера, где вы уже вошли в Instagram' },
  { value: 'settings', label: 'Вставить сессию JSON', title: 'Настройки instagrapi, сохранённые раньше' },
] as const;

/**
 * Instagram login: login + password (or the browser's sessionid, or a session JSON), through a
 * proxy or from the server's IP → a 2FA / challenge code, «подтвердите вход в приложении» (retried by itself) or
 * a manual check in the app with «Повторить» → connected.
 */
export function InstagramConnect(props: ConnectProps) {
  const login = useChannelLogin(props);
  const { personaId, step, setStep, state, channelId } = login;
  const [form, setForm] = useState<InstagramForm>(EMPTY_INSTAGRAM_FORM);
  const [errors, setErrors] = useState<string[]>([]);
  const [code, setCode] = useState('');

  const start = useMutation({
    mutationFn: () => {
      const { body } = instagramRequest(form);
      return channelApi.startInstagram(personaId, body as NonNullable<typeof body>);
    },
    onSuccess: setStep,
  });
  const answer = useMutation({
    mutationFn: (value: string) => channelApi.code(personaId, channelId as number, value),
    onSuccess: (next) => {
      setCode('');
      setStep(next);
    },
  });
  const set = (patch: Partial<InstagramForm>): void => setForm({ ...form, ...patch });

  return (
    <LoginShell label="Подключение Instagram" step={step} onRetry={() => setStep(null)} onCancel={login.cancel}>
      {step === null && (
        <form
          noValidate
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            const checked = instagramRequest(form);
            setErrors(checked.errors);
            if (checked.body) start.mutate();
          }}
        >
          <Segmented<InstagramMethod>
            label="Способ входа"
            value={form.method}
            options={METHODS}
            onChange={(method) => {
              setErrors([]);
              set({ method });
            }}
          />
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {form.method === 'password' && (
              <>
                <Field id="ig-username" label="Логин Instagram">
                  <Input id="ig-username" autoComplete="off" value={form.username} onChange={(e) => set({ username: e.target.value })} />
                </Field>
                <Field id="ig-password" label="Пароль">
                  <Input id="ig-password" type="password" autoComplete="off" value={form.password} onChange={(e) => set({ password: e.target.value })} />
                </Field>
              </>
            )}
            {form.method === 'sessionid' && (
              <Field
                id="ig-sessionid"
                label="sessionid"
                hint="Браузер, где вы вошли в Instagram → инструменты разработчика → Cookies → sessionid. Единственный путь для многих аккаунтов с 2FA."
              >
                <Input id="ig-sessionid" type="password" autoComplete="off" value={form.sessionid} onChange={(e) => set({ sessionid: e.target.value })} />
              </Field>
            )}
            <Field id="ig-proxy" label="Прокси (необязательно)" hint={PROXY_HINT}>
              <Input id="ig-proxy" autoComplete="off" placeholder="socks5://user:pass@host:port" value={form.proxy} onChange={(e) => set({ proxy: e.target.value })} />
            </Field>
          </div>
          {form.method === 'settings' && (
            <Field id="ig-settings" label="Сессия instagrapi (JSON)" hint="Целиком, как её сохранил dump_settings: с authorization_data и uuids устройства.">
              <Textarea id="ig-settings" rows={5} className="font-mono text-xs" value={form.settings_json} onChange={(e) => set({ settings_json: e.target.value })} />
            </Field>
          )}
          {errors.length > 0 && <InlineError>{errors.join('. ')}</InlineError>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={props.onClose}>Отмена</Button>
            <Button type="submit" disabled={start.isPending}>
              {start.isPending ? <Loader2 className="animate-spin" /> : <LogIn />}
              Войти
            </Button>
          </div>
        </form>
      )}

      {(state === 'code' || state === 'challenge') && step && (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (code.trim()) answer.mutate(code.trim());
          }}
        >
          <Field id="ig-code" label={codeLabel(state, step.login.detail)} hint={step.login.message}>
            <Input id="ig-code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" disabled={!code.trim() || answer.isPending}>
              {answer.isPending ? <Loader2 className="animate-spin" /> : <Link2 />}
              Подтвердить
            </Button>
          </div>
        </form>
      )}

      {(state === 'device' || state === 'manual') && step && (
        <div className="flex flex-col gap-3">
          <p className="text-sm">{step.login.message}</p>
          <div className="flex flex-wrap items-center justify-end gap-3">
            {state === 'device' && (
              <span className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
                <Loader2 className="size-3 animate-spin" />
                Жду подтверждения в приложении…
              </span>
            )}
            <Button variant="outline" disabled={answer.isPending} onClick={() => answer.mutate('')}>
              {answer.isPending ? <Loader2 className="animate-spin" /> : <RotateCw />}
              {state === 'device' ? 'Я подтвердил' : 'Повторить'}
            </Button>
          </div>
        </div>
      )}
    </LoginShell>
  );
}
