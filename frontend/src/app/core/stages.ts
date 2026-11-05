import { ChannelKind, ClientStatus, Stage } from './models';

export interface StageMeta {
  id: Stage;
  label: string;
  /** Цвет — CSS-переменная из styles.scss. */
  color: string;
  /** Этап можно ставить вручную (ИИ/оператор). `registered`/`paid` — только данные партнёра. */
  aiReachable: boolean;
}

export const STAGES: readonly StageMeta[] = [
  { id: 'first_contact', label: 'Первый контакт', color: 'var(--sim-stage-1)', aiReachable: true },
  { id: 'engaged', label: 'Вовлечён', color: 'var(--sim-stage-2)', aiReachable: true },
  { id: 'needs_identified', label: 'Потребность', color: 'var(--sim-stage-3)', aiReachable: true },
  { id: 'interested', label: 'Интерес', color: 'var(--sim-stage-4)', aiReachable: true },
  { id: 'handed_to_partner', label: 'У партнёра', color: 'var(--sim-stage-5)', aiReachable: true },
  { id: 'registered', label: 'Регистрация', color: 'var(--sim-stage-6)', aiReachable: false },
  { id: 'paid', label: 'Оплата', color: 'var(--sim-stage-7)', aiReachable: false },
];

export const PARTNER_ONLY_HINT = 'подтверждается только данными партнёра';

export function stageMeta(stage: string): StageMeta {
  return (
    STAGES.find((s) => s.id === stage) ?? {
      id: stage as Stage,
      label: stage,
      color: 'var(--sim-text-muted)',
      aiReachable: false,
    }
  );
}

export const STATUS_LABELS: Record<ClientStatus, string> = {
  active: 'Активен',
  paused: 'Пауза',
  no_response: 'Нет ответа',
  refused: 'Отказ',
};
export const STATUSES = Object.keys(STATUS_LABELS) as ClientStatus[];

export const CHANNEL_LABELS: Record<ChannelKind, string> = {
  telegram: 'Telegram',
  whatsapp: 'WhatsApp',
  instagram: 'Instagram',
};
export const CHANNEL_KINDS = Object.keys(CHANNEL_LABELS) as ChannelKind[];
