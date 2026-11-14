import { renderHook } from '@testing-library/react';
import { useViewportVars, viewportState } from './use-viewport-vars';

describe('viewportState', () => {
  it('reports the visible height and no keyboard when nothing covers the page', () => {
    expect(viewportState(800, 800)).toEqual({ height: 800, keyboard: false });
  });

  it('detects the keyboard when the visible area loses a large part of the page', () => {
    expect(viewportState(800, 480)).toEqual({ height: 480, keyboard: true });
  });

  it('ignores a collapsing browser toolbar and pinch zoom', () => {
    expect(viewportState(800, 740).keyboard).toBe(false);
    expect(viewportState(800, 400, 2)).toEqual({ height: 800, keyboard: false });
  });
});

describe('useViewportVars', () => {
  function fakeVisualViewport(height: number) {
    const listeners = new Set<() => void>();
    const vv = {
      height,
      scale: 1,
      addEventListener: (_: string, fn: () => void) => void listeners.add(fn),
      removeEventListener: (_: string, fn: () => void) => void listeners.delete(fn),
    };
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: vv });
    return { vv, listeners, fire: () => listeners.forEach((fn) => fn()) };
  }

  afterEach(() => {
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: undefined });
  });

  it('publishes --app-vh and marks the open keyboard, then cleans up', () => {
    const root = document.documentElement;
    const { vv, listeners, fire } = fakeVisualViewport(window.innerHeight);
    const { unmount } = renderHook(() => useViewportVars());
    expect(root.style.getPropertyValue('--app-vh')).toBe(`${window.innerHeight}px`);
    expect(root.dataset.keyboard).toBeUndefined();

    vv.height = window.innerHeight - 300;
    fire();
    expect(root.style.getPropertyValue('--app-vh')).toBe(`${window.innerHeight - 300}px`);
    expect(root.dataset.keyboard).toBe('open');

    vv.height = window.innerHeight;
    fire();
    expect(root.dataset.keyboard).toBeUndefined();

    unmount();
    expect(root.style.getPropertyValue('--app-vh')).toBe('');
    expect(listeners.size).toBe(0);
  });

  it('does nothing where the browser has no visualViewport', () => {
    renderHook(() => useViewportVars());
    expect(document.documentElement.style.getPropertyValue('--app-vh')).toBe('');
  });
});
