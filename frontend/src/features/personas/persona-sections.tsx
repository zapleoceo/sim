import type { ReactNode } from 'react';

/** The sections of the persona page, in page order: the anchors of the page navigation. */
export const SECTIONS = [
  { id: 'profile', label: 'Профиль' },
  { id: 'prompt', label: 'Промпт' },
  { id: 'routine', label: 'Распорядок' },
  { id: 'strategy', label: 'Модель и стратегия' },
  { id: 'voice', label: 'Голос и звонки' },
  { id: 'shared', label: 'Общие инструкции' },
  { id: 'agent', label: 'Единый промпт агента' },
  { id: 'assembled', label: 'Итоговый промпт' },
] as const;

export type SectionId = (typeof SECTIONS)[number]['id'];

const LABELS = Object.fromEntries(SECTIONS.map((s) => [s.id, s.label])) as Record<SectionId, string>;

/** One titled section of the persona page; its `id` is the anchor other parts of the page link to. */
export function Section({ id, hint, children }: { id: SectionId; hint?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="flex scroll-mt-4 flex-col gap-4">
      <div>
        <h2 id={`${id}-h`} className="text-base font-semibold">{LABELS[id]}</h2>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

/** Links to the sections that exist on the page (a new persona has only the form). */
export function SectionNav({ ids }: { ids: readonly SectionId[] }) {
  return (
    <nav aria-label="Разделы персонажа" className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {SECTIONS.filter((s) => ids.includes(s.id)).map((s) => (
        <a key={s.id} href={`#${s.id}`} className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
          {s.label}
        </a>
      ))}
    </nav>
  );
}
