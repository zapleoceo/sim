import { createContext, useContext, type ReactNode } from 'react';

const PersonaIdContext = createContext<number | null>(null);

/** The persona whose page is open: life, media library and channels read it from here. */
export function PersonaIdProvider({ id, children }: { id: number; children: ReactNode }) {
  return <PersonaIdContext.Provider value={id}>{children}</PersonaIdContext.Provider>;
}

export function usePersonaId(): number {
  const id = useContext(PersonaIdContext);
  if (id === null) throw new Error('usePersonaId() outside <PersonaIdProvider>');
  return id;
}
