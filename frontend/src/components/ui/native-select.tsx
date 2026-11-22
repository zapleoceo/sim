import { ChevronDownIcon } from 'lucide-react';
import type * as React from 'react';
import { cn } from '@/lib/utils';
import { fieldClass } from './input';

/**
 * A styled native `<select>`: works with react-hook-form `register`, keeps an empty value
 * («не выбрано»), and gives phones their own picker. Its option list takes the theme's colours from
 * index.css (`color-scheme` and the `select option` rule), so it is readable in the dark theme too.
 */
export function NativeSelect({ className, children, ...props }: React.ComponentProps<'select'>) {
  return (
    <div className="relative w-full min-w-0">
      <select
        data-slot="native-select"
        className={cn(fieldClass, 'h-9 appearance-none truncate py-1 pr-8', className)}
        {...props}
      >
        {children}
      </select>
      <ChevronDownIcon
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 opacity-50"
      />
    </div>
  );
}
