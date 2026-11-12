import type { Option } from '@/components/option-select';
import { Badge } from '@/components/ui/badge';
import type { BrokerModel } from '@/lib/models';

/**
 * Model options: the empty «auto» choice first, Broker models (unavailable ones disabled),
 * and the saved value if Broker no longer lists it.
 */
export function modelOptions(models: readonly BrokerModel[], current: string, autoLabel: string): Option<string>[] {
  const out: Option<string>[] = [
    { value: '', label: autoLabel },
    ...models.map((m) => ({
      value: m.id,
      disabled: !m.available,
      label: (
        <span className="flex items-center gap-2">
          {m.name}
          {m.subscription && <Badge variant="muted">подписка</Badge>}
        </span>
      ),
    })),
  ];
  if (current && !models.some((m) => m.id === current)) out.push({ value: current, label: current });
  return out;
}
