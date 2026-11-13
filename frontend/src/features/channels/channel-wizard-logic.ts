import type { ConnectBody } from '@/lib/api/channels';
import type { ChannelKind, ChannelStatus, ConnectState } from '@/lib/models';

export type WizardStep = 'setup' | 'connect' | 'qr' | 'code' | 'password' | 'done';

export const BOT_TOKEN_RE = /^\d+:[\w-]{20,}$/;
export const API_ID_RE = /^\d+$/;
export const API_HASH_RE = /^[0-9a-fA-F]{32}$/;
export const PHONE_RE = /^\+?[\d\s()-]{7,20}$/;
export const PROXY_RE = /^(\w+:\/\/)?\S+:\d+.*$|^$/;

/** QR lives briefly: WhatsApp state is polled this often until the channel settles. */
export const QR_POLL_MS = 3000;

export type BadgeTone = 'success' | 'warning' | 'destructive' | 'muted';

export const STATUS_VIEW: Record<ChannelStatus, { label: string; tone: BadgeTone }> = {
  new: { label: 'Не подключён', tone: 'muted' },
  pending: { label: 'Ожидает подключения', tone: 'warning' },
  active: { label: 'Работает', tone: 'success' },
  challenge: { label: 'Нужно подтверждение', tone: 'warning' },
  expired: { label: 'Сессия истекла', tone: 'destructive' },
  error: { label: 'Ошибка', tone: 'destructive' },
};

/** Where the wizard goes after a connect/verify/state reply. */
export function nextStep(s: ConnectState, kind: ChannelKind, current: WizardStep): WizardStep {
  if (s.status === 'active') return 'done';
  if (s.need) return s.need === '2fa' && kind === 'telegram_user' ? 'password' : 'code';
  if (s.qr_data_uri || (kind === 'whatsapp' && s.status === 'pending')) return 'qr';
  if (s.status === 'error' || s.status === 'expired') return 'connect';
  return current;
}

/** QR polling stops once the channel is active or failed. */
export function pollingDone(s: ConnectState): boolean {
  return s.status === 'active' || s.status === 'error' || s.status === 'expired';
}

export interface ConnectValues {
  bot_token: string;
  api_id: string;
  api_hash: string;
  phone: string;
  username: string;
  password: string;
  proxy: string;
  /** telegram_user login: 'qr' (default, no code) or 'code'. */
  method: string;
}

export const EMPTY_CONNECT: ConnectValues = {
  bot_token: '', api_id: '', api_hash: '', phone: '', username: '', password: '', proxy: '', method: 'qr',
};

export type ConnectErrors = Partial<Record<keyof ConnectValues, string>>;

/**
 * Per-kind validation of the credentials step (same rules as the backend expects). With `saved`
 * credentials an empty field is allowed: the backend reuses the saved value.
 */
export function validateConnect(kind: ChannelKind, v: ConnectValues, saved = false): ConnectErrors {
  if (saved) {
    // only what was typed is checked; empty fields fall back to the saved values
    const full = validateConnect(kind, v);
    return Object.fromEntries(
      Object.entries(full).filter(([k]) => v[k as keyof ConnectValues].trim() !== ''),
    ) as ConnectErrors;
  }
  const e: ConnectErrors = {};
  const proxyBad = v.proxy.trim() !== '' && !PROXY_RE.test(v.proxy.trim());
  if (kind === 'telegram') {
    if (!v.bot_token.trim()) e.bot_token = 'Вставьте токен бота';
    else if (!BOT_TOKEN_RE.test(v.bot_token.trim())) e.bot_token = 'Токен выглядит как 123456:ABC… — проверьте, что скопировали целиком';
  } else if (kind === 'telegram_user') {
    if (!API_ID_RE.test(v.api_id.trim())) e.api_id = 'api_id — только цифры';
    if (!API_HASH_RE.test(v.api_hash.trim())) e.api_hash = 'api_hash — 32 символа (0-9, a-f)';
    if (v.method !== 'qr' && !PHONE_RE.test(v.phone.trim())) e.phone = 'Формат: +380501234567';
    if (proxyBad) e.proxy = 'Формат: socks5://host:port';
  } else if (kind === 'instagram') {
    if (!v.username.trim()) e.username = 'Укажите логин';
    if (!v.password) e.password = 'Укажите пароль';
    if (proxyBad) e.proxy = 'Формат: http://host:port';
  }
  return e;
}

/** Request body per kind. api_id goes as a string (credentials are dict[str, str]); the password is not trimmed. */
export function connectBody(kind: ChannelKind, v: ConnectValues): ConnectBody {
  const proxy = v.proxy.trim() ? { proxy: v.proxy.trim() } : {};
  switch (kind) {
    case 'telegram':
      return { bot_token: v.bot_token.trim() };
    case 'telegram_user':
      return v.method === 'qr'
        ? { api_id: v.api_id.trim(), api_hash: v.api_hash.trim(), method: 'qr', ...proxy }
        : { api_id: v.api_id.trim(), api_hash: v.api_hash.trim(), phone: v.phone.trim(), method: 'code', ...proxy };
    case 'instagram':
      return { username: v.username.trim(), password: v.password, ...proxy };
    case 'whatsapp':
      return {};
  }
}

/** Verification value: the 2FA password is sent as is, a code is trimmed. Empty string = invalid. */
export function verifyValue(step: WizardStep, raw: string): string {
  if (step === 'password') return raw;
  const code = raw.trim();
  return code.length >= 4 ? code : '';
}

/** Saved credentials → form values: only the known fields, everything as text. */
export function prefillConnect(saved: Partial<Record<string, string>>): ConnectValues {
  const out = { ...EMPTY_CONNECT };
  for (const key of Object.keys(EMPTY_CONNECT) as (keyof ConnectValues)[]) {
    const value = saved[key];
    if (typeof value === 'string') out[key] = value;
  }
  return out;
}

/** The form still holds its initial values (nothing typed yet). */
export function sameValues(a: ConnectValues, b: ConnectValues): boolean {
  return (Object.keys(b) as (keyof ConnectValues)[]).every((k) => a[k] === b[k]);
}
