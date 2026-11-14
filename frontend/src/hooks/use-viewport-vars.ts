import { useEffect } from 'react';

/** The on-screen keyboard is considered open when the visible area loses at least this much height. */
export const KEYBOARD_MIN_PX = 120;

export interface ViewportState {
  /** Height of the visible area in CSS px (pinch zoom neutralised). */
  height: number;
  keyboard: boolean;
}

/** Pure part of `useViewportVars`: what the page should know about the visible area. */
export function viewportState(layoutHeight: number, visualHeight: number, scale = 1): ViewportState {
  const height = Math.round(visualHeight * scale);
  return { height, keyboard: layoutHeight - height > KEYBOARD_MIN_PX };
}

/**
 * Publishes the visible viewport to CSS (see index.css):
 * `--app-vh` on <html> — the height that is really visible (the phone keyboard only shrinks the *visual*
 * viewport, `100dvh` does not react to it), and `data-keyboard="open"` while the keyboard is up.
 * Screens that fill the viewport (`data-fit-viewport`, e.g. the inbox) size themselves from `--app-vh`,
 * so the message composer stays above the keyboard; the bottom tab bar hides while typing.
 */
export function useViewportVars(): void {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return undefined;
    const root = document.documentElement;
    const apply = (): void => {
      const state = viewportState(window.innerHeight, vv.height, vv.scale);
      root.style.setProperty('--app-vh', `${state.height}px`);
      if (state.keyboard) {
        root.dataset.keyboard = 'open';
        // iOS scrolls the page under the keyboard; a viewport-filling screen has nothing to scroll to.
        if (document.querySelector('[data-fit-viewport]')) window.scrollTo(0, 0);
      } else {
        delete root.dataset.keyboard;
      }
    };
    apply();
    vv.addEventListener('resize', apply);
    return () => {
      vv.removeEventListener('resize', apply);
      root.style.removeProperty('--app-vh');
      delete root.dataset.keyboard;
    };
  }, []);
}
