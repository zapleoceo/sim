import { useQuery } from '@tanstack/react-query';
import { getRouteApi, useNavigate } from '@tanstack/react-router';
import { Check, Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { InlineError } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { authApi } from '@/lib/api/auth';
import { ApiError } from '@/lib/api/http';
import { authStore } from '@/lib/auth';
import { errorMessage } from '@/lib/errors';
import type { AccessDeniedBody, TelegramWidgetUser } from '@/lib/models';

export const TELEGRAM_WIDGET_SRC = 'https://telegram.org/js/telegram-widget.js?22';

interface TelegramWindow { onTelegramAuth?: (user: TelegramWidgetUser) => void }

/** Sign-in failure -> what to show: the user's Telegram ID to pass to an admin, or a message. */
export function loginFailure(e: unknown): { deniedId: number | null; error: string } {
  if (e instanceof ApiError && e.status === 403) {
    return { deniedId: (e.body as AccessDeniedBody | null)?.telegram_id ?? null, error: '' };
  }
  if (e instanceof ApiError && e.status === 401) {
    return { deniedId: null, error: 'Не удалось подтвердить вход через Telegram. Попробуйте ещё раз.' };
  }
  return { deniedId: null, error: errorMessage(e) };
}

/** Mounts the Telegram Login Widget script into `host`; returns cleanup. */
export function mountTelegramWidget(host: HTMLElement, bot: string, onAuth: (u: TelegramWidgetUser) => void): () => void {
  (window as unknown as TelegramWindow).onTelegramAuth = onAuth;
  const script = document.createElement('script');
  script.async = true;
  script.src = TELEGRAM_WIDGET_SRC;
  script.setAttribute('data-telegram-login', bot);
  script.setAttribute('data-size', 'large');
  script.setAttribute('data-radius', '8');
  script.setAttribute('data-onauth', 'onTelegramAuth(user)');
  host.replaceChildren(script);
  return () => {
    delete (window as unknown as TelegramWindow).onTelegramAuth;
    host.replaceChildren();
  };
}

const route = getRouteApi('/login');

export function LoginPage() {
  const { returnUrl } = route.useSearch();
  const navigate = useNavigate();
  const widget = useRef<HTMLDivElement>(null);
  const [deniedId, setDeniedId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  const config = useQuery({ queryKey: ['auth-config'], queryFn: authApi.config, retry: false, staleTime: Infinity });
  const bot = config.data?.bot_username ?? '';

  useEffect(() => {
    const host = widget.current;
    if (!bot || !host) return;
    return mountTelegramWidget(host, bot, (user) => {
      setError('');
      setDeniedId(null);
      authApi
        .loginTelegram(user)
        .then((state) => {
          authStore.apply(state);
          return returnUrl ? navigate({ href: returnUrl, replace: true }) : navigate({ to: '/', replace: true });
        })
        .catch((e: unknown) => {
          const f = loginFailure(e);
          setDeniedId(f.deniedId);
          setError(f.error);
        });
    });
  }, [bot, navigate, returnUrl]);

  const copyId = (): void => {
    if (deniedId === null) return;
    void navigator.clipboard?.writeText(String(deniedId)).then(() => setCopied(true));
  };

  return (
    <div className="grid min-h-dvh place-items-center bg-muted/40 px-4">
      <div className="flex w-full max-w-sm flex-col items-stretch gap-5 rounded-2xl border bg-card p-8 text-center shadow-sm">
        <div className="flex flex-col items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-primary text-lg font-semibold text-primary-foreground">
            S
          </span>
          <div>
            <h1 className="text-xl font-semibold">Sim</h1>
            <p className="text-sm text-muted-foreground">Вход в админ-панель</p>
          </div>
        </div>

        {config.isSuccess && bot && (
          <div ref={widget} className="flex min-h-11 justify-center" aria-label="Войти через Telegram" />
        )}
        {config.isSuccess && !bot && (
          <div role="status" className="flex flex-col gap-1 text-sm">
            <strong>Вход через Telegram не настроен</strong>
            <span className="text-muted-foreground">
              Задайте SIM_TG_LOGIN_BOT_USERNAME и SIM_TG_LOGIN_BOT_TOKEN на сервере.
            </span>
          </div>
        )}
        {config.isError && <InlineError className="justify-center">Не удалось загрузить настройки входа</InlineError>}

        {deniedId !== null && (
          <div role="alert" className="flex flex-col items-center gap-3 rounded-lg bg-muted p-4 text-sm">
            <p>
              Доступ не выдан. Ваш Telegram ID: <strong className="tabular">{deniedId}</strong> — передайте его
              администратору.
            </p>
            <Button variant="outline" size="sm" onClick={copyId}>
              {copied ? <Check /> : <Copy />}
              {copied ? 'Скопировано' : 'Скопировать ID'}
            </Button>
          </div>
        )}
        {error && <InlineError className="justify-center">{error}</InlineError>}
      </div>
    </div>
  );
}
