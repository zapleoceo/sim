import { ChevronDown } from 'lucide-react';
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState, type MouseEvent, type ReactNode,
} from 'react';
import { cn } from '@/lib/utils';

/** The blocks of the persona page, in page order: from what is edited most often to what is edited rarely. */
export const SECTIONS = [
  { id: 'profile', label: 'Профиль' },
  { id: 'prompt', label: 'Промпт' },
  { id: 'routine', label: 'Распорядок' },
  { id: 'voice', label: 'Голос и звонки' },
  { id: 'strategy', label: 'Модель и стратегия' },
  { id: 'media', label: 'Фото и кружочки' },
  { id: 'assembled', label: 'Контекст модели' },
] as const;

export type SectionId = (typeof SECTIONS)[number]['id'];

/** A flat card of blocks: one border, no shadow; the blocks inside are divided by a line. */
export const BLOCKS = 'flex min-w-0 flex-col rounded-xl border bg-card';

/** The only block that is open when the page opens. */
export const DEFAULT_OPEN: SectionId = 'profile';

const LABELS = Object.fromEntries(SECTIONS.map((s) => [s.id, s.label])) as Record<SectionId, string>;
const isSectionId = (value: string | null | undefined): value is SectionId => SECTIONS.some((s) => s.id === value);

interface SectionsState {
  open: ReadonlySet<SectionId>;
  toggle: (id: SectionId) => void;
  /** Opens the blocks (never closes): for a field error or a link that points inside a block. */
  reveal: (ids: readonly SectionId[]) => void;
}

const SectionsContext = createContext<SectionsState | null>(null);

export function useSections(): SectionsState {
  const state = useContext(SectionsContext);
  if (!state) throw new Error('Section must be rendered inside PersonaSections');
  return state;
}

/** `#anchor` of a link or of the address bar as an element id; a broken escape is not an anchor. */
function anchorOf(hash: string): string {
  try {
    return decodeURIComponent(hash.replace(/^#/, ''));
  } catch {
    return '';
  }
}

/** The block that holds the element with this `id` (a field, a setting, or the block itself). */
function sectionOfAnchor(anchor: string): SectionId | null {
  const id = document.getElementById(anchor)?.closest('[data-section]')?.getAttribute('data-section');
  return isSectionId(id) ? id : null;
}

/**
 * Holds which blocks of the persona page are open. A collapsed block stays in the document (hidden), so
 * its fields keep their values and its anchors exist. A link `#anchor` inside the page (and the hash the
 * page was opened with) opens the block of its target and scrolls to it: «Изменить» in the assembled
 * prompt lands on the field even when its block was collapsed.
 */
export function PersonaSections({ className, children }: { className?: string; children: ReactNode }) {
  const [open, setOpen] = useState<ReadonlySet<SectionId>>(() => new Set([DEFAULT_OPEN]));
  const [target, setTarget] = useState<{ anchor: string } | null>(null);

  const toggle = useCallback((id: SectionId) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);
  const reveal = useCallback((ids: readonly SectionId[]) => {
    setOpen((prev) => (ids.every((id) => prev.has(id)) ? prev : new Set([...prev, ...ids])));
  }, []);

  const goTo = useCallback((anchor: string): boolean => {
    const id = sectionOfAnchor(anchor);
    if (!id) return false;
    reveal([id]);
    setTarget({ anchor });
    return true;
  }, [reveal]);

  // After the block is open (committed), the target has a place on the page to scroll to.
  useEffect(() => {
    if (target) document.getElementById(target.anchor)?.scrollIntoView({ block: 'start' });
  }, [target]);

  useEffect(() => {
    const anchor = anchorOf(window.location.hash);
    if (anchor) goTo(anchor);
  }, [goTo]);

  const onClick = (e: MouseEvent<HTMLDivElement>): void => {
    if (!(e.target instanceof Element)) return;
    const href = e.target.closest('a')?.getAttribute('href');
    if (href?.startsWith('#') && goTo(anchorOf(href))) e.preventDefault();
  };

  const state = useMemo(() => ({ open, toggle, reveal }), [open, toggle, reveal]);
  return (
    <SectionsContext.Provider value={state}>
      <div className={className} onClick={onClick}>{children}</div>
    </SectionsContext.Provider>
  );
}

/**
 * One block of the persona page: a header that folds and unfolds it, and the content. Its `id` is the
 * anchor other parts of the page link to. `invalid` marks a block whose fields did not pass validation.
 */
export function Section({ id, hint, invalid = false, children }: {
  id: SectionId;
  hint?: ReactNode;
  invalid?: boolean;
  children: ReactNode;
}) {
  const { open, toggle } = useSections();
  const isOpen = open.has(id);
  return (
    <section id={id} data-section={id} aria-labelledby={`${id}-h`} className="scroll-mt-4 border-t first:border-t-0">
      <h2>
        <button
          type="button"
          aria-expanded={isOpen}
          aria-controls={`${id}-panel`}
          onClick={() => toggle(id)}
          className="flex min-h-12 w-full items-center gap-3 rounded-xl px-3 py-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring sm:px-5"
        >
          <span id={`${id}-h`} className="min-w-0 flex-1 truncate text-base font-semibold">{LABELS[id]}</span>
          {invalid && <span className="flex-none text-xs font-normal text-destructive">Проверьте поля</span>}
          <ChevronDown aria-hidden className={cn('size-4 flex-none text-muted-foreground transition-transform', isOpen && 'rotate-180')} />
        </button>
      </h2>
      <div id={`${id}-panel`} hidden={!isOpen}>
        <div className="flex min-w-0 flex-col gap-4 px-3 pb-4 sm:px-5">
          {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
          {children}
        </div>
      </div>
    </section>
  );
}
