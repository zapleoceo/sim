import type { LoginStateName } from '@/lib/types';
import type { TelegramLogin } from './api';

export interface LoginForm {
  api_id: string;
  api_hash: string;
  proxy: string;
}

export const EMPTY_LOGIN_FORM: LoginForm = { api_id: '', api_hash: '', proxy: '' };

/** The QR is re-read while it may be scanned; other states wait for the owner. */
export function shouldPoll(state: LoginStateName | undefined): boolean {
  return state === 'qr';
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
  if (proxy && !/^(socks5h?|socks4|https?):\/\/.+:\d+$/i.test(proxy) && !/^[^:/\s]+:\d+$/.test(proxy)) {
    errors.push('Прокси вида socks5://user:pass@host:port');
  }
  return errors.length ? { errors, body: null } : { errors, body: { api_id: apiId, api_hash: apiHash, proxy } };
}

export const CHANNEL_STATUS: Record<string, { label: string; variant: 'success' | 'destructive' | 'muted' | 'warning' }> = {
  connected: { label: 'подключён', variant: 'success' },
  error: { label: 'ошибка', variant: 'destructive' },
  new: { label: 'не подключён', variant: 'warning' },
  disabled: { label: 'выключен', variant: 'muted' },
};

export function channelStatus(status: string): { label: string; variant: 'success' | 'destructive' | 'muted' | 'warning' } {
  return CHANNEL_STATUS[status] ?? { label: status, variant: 'muted' };
}
