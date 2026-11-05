import { HttpParams } from '@angular/common/http';

/** Постраничная выборка списков (контракт API: limit <= 200, offset; ответ `{items, total}`). */
export interface PageRequest { limit: number; offset: number }

export const DEFAULT_PAGE_SIZE = 50;
export const DEFAULT_PAGE: PageRequest = { limit: DEFAULT_PAGE_SIZE, offset: 0 };

/** Собирает HttpParams, отбрасывая пустые значения. */
export function toParams(values: object): HttpParams {
  let params = new HttpParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === '' || value === null || value === undefined || value === false) continue;
    params = params.set(key, String(value));
  }
  return params;
}
