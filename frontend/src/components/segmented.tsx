import type * as React from 'react';
import { tabTriggerClass, tabsListClass } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';

export interface SegmentedOption<T extends string> {
  value: T;
  label: React.ReactNode;
  /** Tooltip / accessible description of the option. */
  title?: string;
}

/** A row of toggle buttons where exactly one is pressed (filters, small enum settings). */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: readonly SegmentedOption<T>[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={cn(tabsListClass, className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          title={o.title}
          aria-pressed={o.value === value}
          data-state={o.value === value ? 'active' : 'inactive'}
          className={tabTriggerClass}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
