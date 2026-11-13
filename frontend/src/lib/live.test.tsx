import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { parseDialogsEvent, useLiveDialogs } from './live';
import { qk } from './query';

class FakeEventSource {
  static last: FakeEventSource | null = null;
  listeners: Record<string, (e: MessageEvent<string>) => void> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(name: string, fn: (e: MessageEvent<string>) => void): void {
    this.listeners[name] = fn;
  }
  removeEventListener(name: string): void {
    delete this.listeners[name];
  }
  close(): void {
    this.closed = true;
  }
  emit(name: string, data: string): void {
    this.listeners[name]?.({ data } as MessageEvent<string>);
  }
}

describe('live dialogs', () => {
  it('parses dialog ids and ignores garbage', () => {
    expect(parseDialogsEvent('{"dialogs":[3,"x",7]}')).toEqual([3, 7]);
    expect(parseDialogsEvent('nope')).toEqual([]);
  });

  it('refreshes the inbox and the changed dialogs when the server pushes an event', () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    const client = new QueryClient();
    const spy = vi.spyOn(client, 'invalidateQueries');
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { unmount } = renderHook(() => useLiveDialogs(), { wrapper });
    const source = FakeEventSource.last!;
    expect(source.url).toBe('/api/events');
    source.emit('dialogs', '{"dialogs":[5]}');
    const keys = spy.mock.calls.map((c) => c[0]?.queryKey);
    expect(keys).toContainEqual(['dialogs']);
    expect(keys).toContainEqual(qk.dialog(5));
    unmount();
    expect(source.closed).toBe(true);
    vi.unstubAllGlobals();
  });
});
