import { fireEvent, render, screen } from '@testing-library/react';
import { useConversationScroll } from './use-conversation-scroll';

function Feed({ count, id = 1 }: { count: number; id?: number }) {
  const scroll = useConversationScroll(id, count);
  return <><div ref={scroll.scroller} data-testid="feed" onScroll={scroll.onScroll} />
    <button onClick={scroll.jump}>Новых: {scroll.pending}</button></>;
}

it('preserves a history reader, ignores non-message updates and jumps only when asked', () => {
  const { rerender } = render(<Feed count={2} />);
  const el = screen.getByTestId('feed');
  Object.defineProperties(el, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 200 } });
  el.scrollTop = 100;
  fireEvent.scroll(el);
  rerender(<Feed count={3} />);
  expect(el.scrollTop).toBe(100);
  expect(screen.getByRole('button')).toHaveTextContent('Новых: 1');
  rerender(<Feed count={3} />);
  expect(el.scrollTop).toBe(100);
  fireEvent.click(screen.getByRole('button'));
  expect(el.scrollTop).toBe(1000);
  expect(screen.getByRole('button')).toHaveTextContent('Новых: 0');
  el.scrollTop = 50;
  fireEvent.scroll(el);
  rerender(<Feed count={3} id={2} />);
  expect(el.scrollTop).toBe(1000);
});
