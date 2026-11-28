import type { ComponentType } from 'react';
import { channelLook } from '@/components/channel-icon';
import type { ChannelKind, ChannelKindInfo } from '@/lib/types';
import type { ConnectProps } from './channel-login';
import { InstagramConnect } from './instagram-connect';
import { TelegramConnect } from './telegram-connect';
import { WhatsAppConnect } from './whatsapp-connect';

/** A messenger in the «Подключить ▾» menu: its look and its connect form (docs/channels.md). */
export interface ChannelKindEntry {
  kind: ChannelKind;
  label: string;
  icon: ReturnType<typeof channelLook>['icon'];
  Connect: ComponentType<ConnectProps>;
  /** What the menu says under the name. */
  hint: string;
}

const entry = (kind: ChannelKind, Connect: ComponentType<ConnectProps>, hint: string): ChannelKindEntry => ({
  kind, ...channelLook(kind), Connect, hint,
});

/** One row per messenger; a new channel = a row here + its connect form. */
export const CHANNEL_KINDS: readonly ChannelKindEntry[] = [
  entry('telegram_user', TelegramConnect, 'личный аккаунт, вход по QR'),
  entry('instagram', InstagramConnect, 'директ, прокси по желанию'),
  entry('whatsapp', WhatsAppConnect, 'связанное устройство, QR'),
];

export function kindEntry(kind: string): ChannelKindEntry | undefined {
  return CHANNEL_KINDS.find((k) => k.kind === kind);
}

/** Whether the server can connect this kind now (`reason` = why not), from GET /channels. */
export function availability(kind: ChannelKind, kinds: ChannelKindInfo[] | undefined): { available: boolean; reason: string } {
  const info = kinds?.find((k) => k.kind === kind);
  return info ? { available: info.available, reason: info.reason } : { available: true, reason: '' };
}
