import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Switch } from '@/components/ui/switch';
import { qk } from '@/lib/query';
import { chatsApi } from './api';

/** «Отвечать сразу» in the chat header: this chat skips her phone checks and she answers at
 * once, like «Всегда сразу» (docs/attention.md; for testing). */
export function AttentionInstantSwitch({ chatId, enabled }: { chatId: number; enabled: boolean }) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: (next: boolean) => chatsApi.setAttentionInstant(chatId, next),
    onSuccess: (_row, next) => {
      toast.success(next ? 'В этом чате она отвечает сразу' : 'В этом чате она отвечает как живой человек');
      void queryClient.invalidateQueries({ queryKey: qk.chat(chatId) });
      void queryClient.invalidateQueries({ queryKey: qk.chatStatus(chatId) });
      void queryClient.invalidateQueries({ queryKey: qk.chatLists });
    },
  });
  const checked = toggle.isPending ? (toggle.variables ?? enabled) : enabled;
  return (
    <label className="flex items-center gap-2 text-sm" title="Без пауз «Внимания»: для проверки">
      <Switch checked={checked} onCheckedChange={(v) => toggle.mutate(v)} disabled={toggle.isPending} />
      Отвечать сразу
    </label>
  );
}
