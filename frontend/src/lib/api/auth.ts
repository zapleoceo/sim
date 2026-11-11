import type { AuthConfig, AuthState, TelegramWidgetUser } from '../models';
import { http } from './http';

export const authApi = {
  /** Session probe by cookie: no toast, no redirect on 401. */
  me: () => http.get<AuthState>('/api/auth/me', { silent: true, noAuthRedirect: true }),
  config: () => http.get<AuthConfig>('/api/auth/config', { silent: true }),
  /** 403 (no access) and 401 are handled by the sign-in page. */
  loginTelegram: (user: TelegramWidgetUser) => http.post<AuthState>('/api/auth/telegram', user, { silent: true }),
  logout: () => http.post<void>('/api/auth/logout', {}, { silent: true, noAuthRedirect: true }),
};
