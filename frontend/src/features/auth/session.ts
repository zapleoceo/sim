import { useSyncExternalStore } from 'react';
import { api, setCsrfToken } from '@/lib/api';
import type { Admin, AuthConfig, AuthState, TelegramWidgetUser } from '@/lib/types';

export const authApi = {
  config: () => api.get<AuthConfig>('/auth/config', { noAuthRedirect: true }),
  me: () => api.get<AuthState>('/auth/me', { noAuthRedirect: true }),
  loginTelegram: (user: TelegramWidgetUser) => api.post<AuthState>('/auth/telegram', user, { noAuthRedirect: true }),
  logout: () => api.post<void>('/auth/logout', undefined, { noAuthRedirect: true }),
};

let admin: Admin | null = null;
const listeners = new Set<() => void>();

function set(next: Admin | null, csrf: string | null): void {
  admin = next;
  setCsrfToken(csrf);
  listeners.forEach((l) => l());
}

/** Session outside React, so the router guard and the HTTP layer can read it. */
export const session = {
  get: (): Admin | null => admin,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  apply(state: AuthState): void {
    set(state.admin, state.csrf);
  },
  clear(): void {
    if (admin) set(null, null);
    else setCsrfToken(null);
  },
  /** Restores the session from the cookie once; true when signed in. */
  async restore(): Promise<boolean> {
    if (admin) return true;
    try {
      session.apply(await authApi.me());
      return true;
    } catch {
      session.clear();
      return false;
    }
  },
  async logout(): Promise<void> {
    try {
      await authApi.logout();
    } finally {
      session.clear();
    }
  },
};

export function useAdmin(): Admin | null {
  return useSyncExternalStore(session.subscribe, session.get, session.get);
}
