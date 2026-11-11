import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';
import type { PageRequest } from '@/lib/api/http';
import { OptionSelect } from './option-select';
import { Button } from './ui/button';

export const PAGE_SIZES = [25, 50, 100] as const;

export interface PagerState { index: number; size: number; pages: number; from: number; to: number }

/** Page math for `{limit, offset}` lists: 1-based range and page count. */
export function pagerState(page: PageRequest, total: number): PagerState {
  const pages = Math.max(1, Math.ceil(total / page.limit));
  const index = Math.min(Math.floor(page.offset / page.limit), pages - 1);
  return {
    index,
    size: page.limit,
    pages,
    from: total === 0 ? 0 : page.offset + 1,
    to: Math.min(page.offset + page.limit, total),
  };
}

/** «1–25 из 120», page size and first/prev/next/last. */
export function Pager({
  page,
  total,
  onChange,
  label = 'Страницы',
}: {
  page: PageRequest;
  total: number;
  onChange: (page: PageRequest) => void;
  label?: string;
}) {
  const s = pagerState(page, total);
  const go = (index: number): void => onChange({ limit: s.size, offset: index * s.size });
  return (
    <nav aria-label={label} className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 text-sm">
      <div className="flex items-center gap-2 text-muted-foreground">
        <span className="hidden sm:inline">На странице</span>
        <OptionSelect
          aria-label="Записей на странице"
          className="h-8 w-20"
          value={s.size}
          options={PAGE_SIZES.map((n) => ({ value: n, label: String(n) }))}
          onChange={(size) => onChange({ limit: size, offset: 0 })}
        />
      </div>
      <span className="text-muted-foreground tabular" role="status">
        {s.from}–{s.to} из {total}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon-sm" aria-label="Первая страница" disabled={s.index === 0} onClick={() => go(0)}>
          <ChevronsLeft />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Предыдущая страница" disabled={s.index === 0} onClick={() => go(s.index - 1)}>
          <ChevronLeft />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Следующая страница" disabled={s.index >= s.pages - 1} onClick={() => go(s.index + 1)}>
          <ChevronRight />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Последняя страница" disabled={s.index >= s.pages - 1} onClick={() => go(s.pages - 1)}>
          <ChevronsRight />
        </Button>
      </div>
    </nav>
  );
}
