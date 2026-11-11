import type * as React from 'react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';

export type OptionValue = string | number | null;
export interface Option<V extends OptionValue> { value: V; label: React.ReactNode; disabled?: boolean }

/** Radix Select forbids '' as an item value: empty/null map to this token. */
const EMPTY = '__empty__';

function encode(v: OptionValue): string {
  return v === null || v === '' ? EMPTY : `${typeof v === 'number' ? 'n' : 's'}:${v}`;
}

export function decodeOption<V extends OptionValue>(raw: string, options: readonly Option<V>[]): V {
  return (options.find((o) => encode(o.value) === raw)?.value ?? options[0]?.value) as V;
}

/**
 * Select over typed options (numbers, strings, null for «Все»).
 * Values round-trip exactly, so `null` persona or `''` stage stay as they are in filters.
 */
export function OptionSelect<V extends OptionValue>({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  className,
  id,
  'aria-label': ariaLabel,
  'aria-invalid': ariaInvalid,
  'aria-describedby': ariaDescribedBy,
}: {
  value: V;
  onChange: (value: V) => void;
  options: readonly Option<V>[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  id?: string;
  'aria-label'?: string;
  'aria-invalid'?: boolean;
  'aria-describedby'?: string;
}) {
  const known = options.some((o) => encode(o.value) === encode(value));
  return (
    <Select
      value={known ? encode(value) : undefined}
      onValueChange={(raw) => onChange(decodeOption(raw, options))}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        className={className}
        aria-label={ariaLabel}
        aria-invalid={ariaInvalid}
        aria-describedby={ariaDescribedBy}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={encode(o.value)} value={encode(o.value)} disabled={o.disabled}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
