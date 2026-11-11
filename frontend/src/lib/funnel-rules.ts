import type { Stage } from './models';
import { PARTNER_ONLY_HINT, stageMeta } from './stages';

export type DropVerdict = { ok: true } | { ok: false; reason: string };

/**
 * Правила переноса карточки между колонками воронки.
 * `registered` и `paid` ставит только партнёр: вручную — запрещено (API вернёт 422).
 */
export function checkStageDrop(from: Stage, to: Stage): DropVerdict {
  if (from === to) return { ok: false, reason: '' };
  const target = stageMeta(to);
  if (!target.aiReachable) {
    return { ok: false, reason: `Этап «${target.label}» ${PARTNER_ONLY_HINT}` };
  }
  if (!stageMeta(from).aiReachable) {
    return { ok: false, reason: 'Клиента с регистрацией или оплатой вручную не перемещаем' };
  }
  return { ok: true };
}
