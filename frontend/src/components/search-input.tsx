import { Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { Input } from './ui/input';

export const SEARCH_DELAY_MS = 300;

/**
 * The search box of chats and leads. Typing is local; `onChange` gets the trimmed text once the
 * typing pauses (it goes to the address, `?q=`). A new `value` from outside (back, a link)
 * replaces the text.
 */
export function SearchInput({ value, onChange, label, placeholder, className }: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  placeholder?: string;
  className?: string;
}) {
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  if (value !== seen) {
    setSeen(value);
    if (value !== text.trim()) setText(value);
  }
  useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <label className={cn('relative block', className)}>
      <span className="sr-only">{label}</span>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        value={text}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => onChange(next.trim()), SEARCH_DELAY_MS);
        }}
        placeholder={placeholder}
        className="pl-8"
      />
    </label>
  );
}
