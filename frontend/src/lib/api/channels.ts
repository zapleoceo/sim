import type { Channel, ChannelIn, ConnectState, ListResponse } from '../models';
import { http } from './http';

export interface ConnectBody {
  bot_token?: string;
  username?: string;
  password?: string;
  api_id?: string;
  api_hash?: string;
  phone?: string;
  /** telegram_user: `qr` — scan a QR in Telegram → Devices (no code); `code` — code to the app. */
  method?: 'qr' | 'code';
  proxy?: string;
}

export const channelsApi = {
  list: () => http.get<ListResponse<Channel>>('/api/channels').then((r) => r.items),
  create: (body: ChannelIn) => http.post<Channel>('/api/channels', body),
  remove: (id: number) => http.delete(`/api/channels/${id}`),
  connect: (id: number, body: ConnectBody) => http.post<ConnectState>(`/api/channels/${id}/connect`, body),
  verify: (id: number, code: string) => http.post<ConnectState>(`/api/channels/${id}/verify`, { code }),
  /** Saved connect credentials (owner-only), to prefill the connect form. */
  credentials: (id: number) => http.get<Partial<Record<string, string>>>(`/api/channels/${id}/credentials`, { silent: true }),
  state: (id: number) => http.get<ConnectState>(`/api/channels/${id}/state`, { silent: true }),
};
