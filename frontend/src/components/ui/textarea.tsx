import type * as React from 'react';
import { cn } from '@/lib/utils';
import { fieldClass } from './input';

/** Grows with its content (CSS field-sizing) up to `max-h-*` from the caller. */
export function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(fieldClass, 'field-sizing-content min-h-16 py-2 leading-relaxed', className)}
      {...props}
    />
  );
}
