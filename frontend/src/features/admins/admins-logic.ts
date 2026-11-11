import type { AdminCreate, AdminUser, Role } from '@/lib/models';

export const ROLES: readonly Role[] = ['superadmin', 'owner', 'operator'];

/** Numeric Telegram ID, or @username of 3–32 characters. */
export const ADMIN_TARGET_RE = /^(\d+|@?[A-Za-z0-9_]{3,32})$/;

/** «123456» -> telegram_id, «@name» / «name» -> tg_username. */
export function parseAdminTarget(raw: string): Pick<AdminCreate, 'telegram_id' | 'tg_username'> {
  const value = raw.trim();
  return /^\d+$/.test(value) ? { telegram_id: Number(value) } : { tg_username: value.replace(/^@/, '') };
}

export function adminTargetError(raw: string): string {
  const value = raw.trim();
  if (!value) return 'Укажите Telegram ID или @username';
  return ADMIN_TARGET_RE.test(value) ? '' : 'Числовой ID или @username (3–32 символа)';
}

export function adminName(a: Pick<AdminUser, 'display_name' | 'tg_username' | 'telegram_id'>): string {
  return a.display_name || a.tg_username || String(a.telegram_id ?? '');
}
