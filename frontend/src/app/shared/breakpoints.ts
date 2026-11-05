import { BreakpointObserver } from '@angular/cdk/layout';
import { Signal, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs';

/** Единственная точка перелома телефон/десктоп (дублируется в CSS как max-width: 767px). */
export const PHONE_QUERY = '(max-width: 767px)';

/** Планшет: боковая навигация сворачивается в рейку с иконками (768–1199px). */
export const RAIL_QUERY = '(min-width: 768px) and (max-width: 1199px)';

/** Диалоги: ниже 1024px — одна панель (список ИЛИ переписка), как на телефоне. */
export const SINGLE_PANE_QUERY = '(max-width: 1023px)';

/** Широкий десктоп: включает правую панель карточки клиента в диалогах. */
export const WIDE_QUERY = '(min-width: 1280px)';

/** Сигнал «совпадает ли media query». Вызывать в injection context. */
export function injectMediaQuery(query: string): Signal<boolean> {
  return toSignal(
    inject(BreakpointObserver)
      .observe(query)
      .pipe(map((s) => s.matches)),
    { initialValue: false },
  );
}

export function injectIsPhone(): Signal<boolean> {
  return injectMediaQuery(PHONE_QUERY);
}
