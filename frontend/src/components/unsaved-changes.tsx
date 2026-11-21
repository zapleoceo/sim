import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';

/** Adds one unsaved section by name; returns its removal. */
type Register = (name: string) => () => void;

const RegisterContext = createContext<Register | null>(null);

/** Names in the order they became unsaved, each once (two day cards are one «День»). */
export function uniqueNames(entries: readonly string[]): string[] {
  return [...new Set(entries)];
}

/**
 * A page with several separately saved sections (persona page, lead card): the sections register
 * their unsaved state here and the page has ONE guard that names them («Не сохранено: Агенты, Мир»).
 */
export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<readonly string[]>([]);
  const register = useCallback<Register>((name) => {
    setEntries((list) => [...list, name]);
    return () =>
      setEntries((list) => {
        const i = list.indexOf(name);
        return i < 0 ? list : [...list.slice(0, i), ...list.slice(i + 1)];
      });
  }, []);
  const names = useMemo(() => uniqueNames(entries), [entries]);
  useUnsavedGuard(names.length > 0, names);
  return <RegisterContext.Provider value={register}>{children}</RegisterContext.Provider>;
}

/**
 * Marks a section unsaved while `dirty`. Inside `UnsavedChangesProvider` the page's one guard
 * covers it; on its own it gets its own guard.
 */
export function useUnsavedSection(name: string, dirty: boolean): void {
  const register = useContext(RegisterContext);
  useUnsavedGuard(dirty && register === null, [name]);
  useEffect(() => (register && dirty ? register(name) : undefined), [register, name, dirty]);
}
