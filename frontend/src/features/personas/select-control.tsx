import { useController, type Control, type FieldPath, type FieldValues } from 'react-hook-form';
import { OptionSelect, type Option, type OptionValue } from '@/components/option-select';

/**
 * OptionSelect bound to a react-hook-form field. Accepts the `id`/aria props that `Field` injects,
 * so the label and errors stay wired to the select trigger.
 */
export function SelectControl<T extends FieldValues, N extends FieldPath<T>>({
  control,
  name,
  options,
  placeholder,
  id,
  'aria-invalid': ariaInvalid,
  'aria-describedby': ariaDescribedBy,
}: {
  control: Control<T>;
  name: N;
  options: readonly Option<OptionValue>[];
  placeholder?: string;
  id?: string;
  'aria-invalid'?: boolean;
  'aria-describedby'?: string;
}) {
  const { field } = useController({ control, name });
  return (
    <OptionSelect
      id={id}
      aria-invalid={ariaInvalid}
      aria-describedby={ariaDescribedBy}
      value={field.value as OptionValue}
      onChange={field.onChange}
      options={options}
      placeholder={placeholder}
    />
  );
}
