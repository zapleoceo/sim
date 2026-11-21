import { useWatch, type Control, type UseFormRegister } from 'react-hook-form';
import { NativeSelect } from '@/components/ui/native-select';
import type { BrokerModel } from '@/lib/types';
import { cn } from '@/lib/utils';
import { availabilityLabel, modelOptions, type PurposeInfo } from './model-filter';
import type { PersonaFormValues } from './persona-form-logic';

const DOT: Record<string, string> = {
  true: 'bg-success',
  false: 'bg-destructive',
  null: 'bg-muted-foreground/50',
};

/** One model select: only models that fit the purpose; the saved value is always kept. */
export function ModelSelect({
  info,
  models,
  control,
  register,
}: {
  info: PurposeInfo;
  models: readonly BrokerModel[];
  control: Control<PersonaFormValues>;
  register: UseFormRegister<PersonaFormValues>;
}) {
  const current = useWatch({ control, name: info.field }) as string;
  const options = modelOptions(models, info.purpose, current);
  const selected = options.find((o) => o.value === current);
  const id = `persona-${info.field}`;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">{info.label}</label>
      <NativeSelect id={id} {...register(info.field)}>
        <option value="">— не выбрана —</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </NativeSelect>
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {selected ? (
          <>
            <span className={cn('size-2 flex-none rounded-full', DOT[String(selected.available)])} aria-hidden="true" />
            <span className={cn(selected.outsideFilter && 'text-warning')}>
              {selected.outsideFilter ? 'Не из списка подходящих — проверьте' : availabilityLabel(selected.available)}
            </span>
          </>
        ) : (
          <span>{info.hint}</span>
        )}
      </p>
    </div>
  );
}
