import { MutationSwitch } from '@/components/mutation-switch';
import { useUpdateClient } from './api';

/** The client's «ИИ отвечает» switch. Same field and component in the table and on the card. */
export function ClientAiSwitch({ clientId, enabled, name, withLabel = false }: {
  clientId: number;
  enabled: boolean;
  name: string;
  withLabel?: boolean;
}) {
  const update = useUpdateClient(clientId, (p) => (p.ai_enabled ? `ИИ включён: ${name}` : `ИИ выключен: ${name}`));
  return (
    <MutationSwitch
      checked={enabled}
      pending={update.isPending}
      pendingValue={update.variables?.ai_enabled}
      onCheckedChange={(v) => update.mutate({ ai_enabled: v })}
      label={withLabel ? 'ИИ отвечает этому клиенту' : undefined}
      aria-label={withLabel ? undefined : `ИИ отвечает клиенту ${name}`}
    />
  );
}
