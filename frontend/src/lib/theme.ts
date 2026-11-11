import { useSyncExternalStore } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';

const KEY = 'sim-theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';
const listeners = new Set<() => void>();

function read(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}

export function resolveDark(mode: ThemeMode, systemDark: boolean): boolean {
  return mode === 'dark' || (mode === 'system' && systemDark);
}

function systemDark(): boolean {
  return typeof matchMedia === 'function' && matchMedia(DARK_QUERY).matches;
}

/** Applies the theme to <html>. index.html runs the same logic before first paint. */
export function applyTheme(mode: ThemeMode = read()): void {
  document.documentElement.classList.toggle('dark', resolveDark(mode, systemDark()));
}

export function setTheme(mode: ThemeMode): void {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* private mode: theme still applies for this page */
  }
  applyTheme(mode);
  listeners.forEach((l) => l());
}

/** Follow OS changes while in «system» mode. */
export function watchSystemTheme(): () => void {
  if (typeof matchMedia !== 'function') return () => undefined;
  const mq = matchMedia(DARK_QUERY);
  const onChange = (): void => applyTheme();
  mq.addEventListener('change', onChange);
  return () => mq.removeEventListener('change', onChange);
}

export function useTheme(): ThemeMode {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    read,
    () => 'system',
  );
}
