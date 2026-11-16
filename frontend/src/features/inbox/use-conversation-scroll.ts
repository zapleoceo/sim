import { useEffect, useRef, useState } from 'react';

export function useConversationScroll(dialogId: number, count: number) {
  const scroller = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const previous = useRef({ dialogId, count });
  const [pending, setPending] = useState(0);
  const jump = () => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
    followBottom.current = true;
    setPending(0);
  };
  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    followBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
    if (followBottom.current) setPending(0);
  };
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const last = previous.current;
    if (last.dialogId !== dialogId || followBottom.current) {
      el.scrollTop = el.scrollHeight;
      setPending(0);
      followBottom.current = true;
    } else if (count > last.count) setPending(n => n + count - last.count);
    previous.current = { dialogId, count };
  }, [count, dialogId]);
  return { scroller, pending, onScroll, jump };
}
