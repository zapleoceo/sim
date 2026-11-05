import { HttpParams } from '@angular/common/http';

/** Собирает HttpParams, отбрасывая пустые значения. */
export function toParams(values: object): HttpParams {
  let params = new HttpParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === '' || value === null || value === undefined || value === false) continue;
    params = params.set(key, String(value));
  }
  return params;
}
