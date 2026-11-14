import { vi } from 'vitest';

export const PHONE_WIDTH = 380;

/**
 * Makes `matchMedia` answer width queries as a screen `width` px wide (jsdom has no layout).
 * Restored after each test (`unstubGlobals`). Non-width queries (colour scheme) answer false.
 */
export function stubViewportWidth(width: number): void {
  vi.stubGlobal('matchMedia', (query: string) => {
    const max = /max-width:\s*(\d+)px/.exec(query);
    const min = /min-width:\s*(\d+)px/.exec(query);
    const matches = Boolean(max || min) && (!max || width <= Number(max[1])) && (!min || width >= Number(min[1]));
    return {
      matches,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    };
  });
}
