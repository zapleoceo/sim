import type { LoginStateName } from '@/lib/types';
import type { InstagramLogin, TelegramLogin } from './api';

export interface LoginForm {
  api_id: string;
  api_hash: string;
  proxy: string;
}

export const EMPTY_LOGIN_FORM: LoginForm = { api_id: '', api_hash: '', proxy: '' };

/** The dashboard re-reads the login while a QR may be scanned or the Instagram app approval is awaited. */
export function shouldPoll(state: LoginStateName | undefined): boolean {
  return state === 'qr' || state === 'device';
}

/** States in which the owner still has something to do (the login can be cancelled). */
export function inProgress(state: LoginStateName | undefined): boolean {
  return state !== undefined && !['connected', 'error', 'none'].includes(state);
}

const PROXY = /^(socks5h?|socks4|https?):\/\/.+:\d+$/i;
const BARE_PROXY = /^[^:/\s]+:\d+$/;

function proxyOk(proxy: string): boolean {
  return PROXY.test(proxy) || BARE_PROXY.test(proxy);
}

/** Form errors in Russian, or the request body when the form is fine. */
export function loginRequest(form: LoginForm): { errors: string[]; body: TelegramLogin | null } {
  const errors: string[] = [];
  const apiId = Number(form.api_id.trim());
  if (!/^\d+$/.test(form.api_id.trim()) || !Number.isSafeInteger(apiId) || apiId <= 0) {
    errors.push('api_id — целое число с my.telegram.org');
  }
  const apiHash = form.api_hash.trim();
  if (!/^[0-9a-fA-F]{8,64}$/.test(apiHash)) errors.push('api_hash — строка из букв a–f и цифр с my.telegram.org');
  const proxy = form.proxy.trim();
  if (proxy && !proxyOk(proxy)) errors.push('Прокси вида socks5://user:pass@host:port');
  return errors.length ? { errors, body: null } : { errors, body: { api_id: apiId, api_hash: apiHash, proxy } };
}

// --- Instagram -------------------------------------------------------------------------------

/** How the owner logs in: login + password, the browser's sessionid cookie, or a session JSON. */
export type InstagramMethod = 'password' | 'sessionid' | 'settings';

export interface InstagramForm {
  method: InstagramMethod;
  username: string;
  password: string;
  proxy: string;
  sessionid: string;
  settings_json: string;
}

export const EMPTY_INSTAGRAM_FORM: InstagramForm = {
  method: 'password', username: '', password: '', proxy: '', sessionid: '', settings_json: '',
};

export const PROXY_HINT =
  'Пусто — вход с IP сервера (Германия). С прокси: страна прокси — страна аккаунта, и прокси потом не менять.';

/** Form errors in Russian, or the body of the chosen way in (only its own fields are sent). */
export function instagramRequest(form: InstagramForm): { errors: string[]; body: InstagramLogin | null } {
  const errors: string[] = [];
  const proxy = form.proxy.trim();
  if (proxy && !proxyOk(proxy)) errors.push('Прокси вида socks5://user:pass@host:port');
  let body: InstagramLogin = { proxy };
  if (form.method === 'password') {
    const username = form.username.trim().replace(/^@/, '');
    if (!/^[A-Za-z0-9._]{1,30}$/.test(username)) errors.push('Логин Instagram — буквы, цифры, точка, подчёркивание');
    if (!form.password) errors.push('Введите пароль');
    body = { ...body, username, password: form.password };
  } else if (form.method === 'sessionid') {
    const sessionid = form.sessionid.trim();
    if (!/^\d+(%3A|:)\S+$/.test(sessionid)) errors.push('sessionid — значение cookie «sessionid» из браузера (начинается с цифр и %3A)');
    body = { ...body, sessionid };
  } else {
    const text = form.settings_json.trim();
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
    if (!parsed || typeof parsed !== 'object' || !('authorization_data' in parsed)) {
      errors.push('Вставьте JSON сессии instagrapi (с authorization_data)');
    }
    body = { ...body, settings_json: text };
  }
  return errors.length ? { errors, body: null } : { errors, body };
}

/** The code field's label for the Instagram question (2FA or a challenge). */
export function codeLabel(state: LoginStateName | undefined, detail: string | null | undefined): string {
  if (state === 'challenge') return detail === 'email' ? 'Код из письма Instagram' : 'Код из SMS от Instagram';
  return detail === 'totp' ? 'Код из приложения-аутентификатора' : 'Код из SMS';
}

// --- statuses --------------------------------------------------------------------------------

export type StatusVariant = 'success' | 'destructive' | 'muted' | 'warning';

export const CHANNEL_STATUS: Record<string, { label: string; variant: StatusVariant }> = {
  connected: { label: 'подключён', variant: 'success' },
  error: { label: 'ошибка', variant: 'destructive' },
  new: { label: 'не подключён', variant: 'warning' },
  pending: { label: 'идёт вход', variant: 'warning' },
  challenge: { label: 'нужен повторный вход', variant: 'destructive' },
  disabled: { label: 'выключен', variant: 'muted' },
};

export function channelStatus(status: string): { label: string; variant: StatusVariant } {
  return CHANNEL_STATUS[status] ?? { label: status, variant: 'muted' };
}

/** A channel whose account must log in again (Instagram checkpoint, WhatsApp unlinked on the phone). */
export function needsRelogin(status: string): boolean {
  return status === 'challenge' || status === 'error';
}
