import { useQuery } from '@tanstack/react-query';
import { getRouteApi, useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { InlineError } from '@/components/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { qk } from '@/lib/query';
import type { TelegramWidgetUser } from '@/lib/types';
import { authApi, session } from './session';

export const TELEGRAM_WIDGET_SRC = 'https://telegram.org/js/telegram-widget.js?22';

interface TelegramWindow { onTelegramAuth?: (user: TelegramWidgetUser) => void }

/** Mounts the Telegram Login Widget script into `host`; returns the cleanup. */
export function mountTelegramWidget(host: HTMLElement, bot: string, onAuth: (u: TelegramWidgetUser) => void): () => void {
  (window as unknown as TelegramWindow).onTelegramAuth = onAuth;
  const script = document.createElement('script');
  script.async = true;
  script.src = TELEGRAM_WIDGET_SRC;
  script.setAttribute('data-telegram-login', bot);
  script.setAttribute('data-size', 'large');
  script.setAttribute('data-radius', '8');
  script.setAttribute('data-request-access', 'write');
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
  const [error, setError] = useState('');

  const config = useQuery({ queryKey: qk.authConfig, queryFn: authApi.config, retry: false, staleTime: Infinity });
  const bot = config.data?.bot_username ?? '';

  useEffect(() => {
    const host = widget.current;
    if (!bot || !host) return undefined;
    return mountTelegramWidget(host, bot, (user) => {
      setError('');
      authApi
        .loginTelegram(user)
        .then((state) => {
          session.apply(state);
          return returnUrl ? navigate({ href: returnUrl, replace: true }) : navigate({ to: '/chats', replace: true });
        })
        .catch((e: unknown) => setError(errorMessage(e)));
    });
  }, [bot, navigate, returnUrl]);

  return (
    <div className="grid min-h-dvh place-items-center bg-muted/40 px-4">
      <div className="flex w-full max-w-sm flex-col items-stretch gap-5 rounded-2xl border bg-card p-8 text-center shadow-sm">
        <div className="flex flex-col items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-primary text-lg font-semibold text-primary-foreground">
            S
          </span>
          <div>
            <h1 className="text-xl font-semibold">Sim</h1>
            <p className="text-sm text-muted-foreground">Вход в дашборд через Telegram</p>
          </div>
        </div>

        {config.isPending && <Skeleton className="mx-auto h-11 w-56" />}
        {config.isSuccess && bot && (
          <div ref={widget} className="flex min-h-11 justify-center" aria-label="Войти через Telegram" />
        )}
        {config.isSuccess && !bot && (
          <p role="status" className="text-sm text-muted-foreground">
            Вход через Telegram не настроен на сервере.
          </p>
        )}
        {config.isError && <InlineError className="justify-center">{errorMessage(config.error)}</InlineError>}
        {error && <InlineError className="justify-center">{error}</InlineError>}
      </div>
    </div>
  );
}
