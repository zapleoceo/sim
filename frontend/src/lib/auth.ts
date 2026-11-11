import { useSyncExternalStore } from 'react';
import { authApi } from './api/auth';
import type { AuthState, Role, User } from './models';

/** Owner rights: owner and superadmin (mirrors Role.has_owner_rights on the backend). */
export function hasOwnerRights(role: Role | undefined): boolean {
  return role === 'owner' || role === 'superadmin';
}

/** Where a signed-in user lands on `/`. */
export function defaultLanding(role: Role | undefined): '/studio' | '/inbox' {
  return hasOwnerRights(role) ? '/studio' : '/inbox';
}

interface Snapshot { user: User | null; csrf: string | null }

let state: Snapshot = { user: null, csrf: null };
const listeners = new Set<() => void>();

function set(next: Snapshot): void {
  state = next;
  listeners.forEach((l) => l());
}

/** Session state outside React, so the router guards and the HTTP layer can read it. */
export const authStore = {
  get: (): Snapshot => state,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  apply(s: AuthState): void {
    set({ user: s.user, csrf: s.csrf_token });
  },
  clear(): void {
    if (state.user || state.csrf) set({ user: null, csrf: null });
  },
  /** Restores the session from the cookie once; true when signed in. */
  async restore(): Promise<boolean> {
    if (state.user) return true;
    try {
      authStore.apply(await authApi.me());
      return true;
    } catch {
      authStore.clear();
      return false;
    }
  },
  async logout(): Promise<void> {
    try {
      await authApi.logout();
    } finally {
      authStore.clear();
    }
  },
};

export function useAuth(): Snapshot & { isOwner: boolean; isSuperadmin: boolean } {
  const s = useSyncExternalStore(authStore.subscribe, authStore.get, authStore.get);
  return { ...s, isOwner: hasOwnerRights(s.user?.role), isSuperadmin: s.user?.role === 'superadmin' };
}
