import { useEffect, useRef } from 'react';

/**
 * Calls `onVisible` when the returned sentinel element scrolls into view (infinite lists).
 * Without IntersectionObserver (old browsers, jsdom) nothing happens: the caller keeps a button.
 */
export function useInView<T extends Element>(onVisible: () => void, enabled: boolean) {
  const ref = useRef<T>(null);
  const callback = useRef(onVisible);
  useEffect(() => {
    callback.current = onVisible;
  });
  useEffect(() => {
    const el = ref.current;
    if (!enabled || !el || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) callback.current();
    }, { rootMargin: '200px' });
    io.observe(el);
    return () => io.disconnect();
  }, [enabled]);
  return ref;
}
