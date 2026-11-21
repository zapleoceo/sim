import { Switch } from '@/components/ui/switch';
import { useUpdateClient } from './api';

/** The client's «ИИ отвечает» switch. Same field and component in the table and on the card. */
export function ClientAiSwitch({ clientId, enabled, name, withLabel = false }: {
  clientId: number;
  enabled: boolean;
  name: string;
  withLabel?: boolean;
}) {
  const update = useUpdateClient(clientId, (p) => (p.ai_enabled ? `ИИ включён: ${name}` : `ИИ выключен: ${name}`));
  const checked = update.isPending && update.variables?.ai_enabled !== undefined ? update.variables.ai_enabled : enabled;
  const control = (
    <Switch
      checked={checked}
      disabled={update.isPending}
      onCheckedChange={(v) => update.mutate({ ai_enabled: v })}
      aria-label={withLabel ? undefined : `ИИ отвечает клиенту ${name}`}
    />
  );
  if (!withLabel) return control;
  return (
    <label className="flex items-center gap-2 text-sm">
      {control}
      ИИ отвечает этому клиенту
    </label>
  );
}
