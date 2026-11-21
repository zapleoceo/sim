import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Switch } from '@/components/ui/switch';
import { qk } from '@/lib/query';
import { chatsApi, type ChatFlag } from './api';

/** The chat header's own switches, one definition each (docs/frontend.md). */
const FLAGS: Record<ChatFlag, { label: string; title: string; on: string; off: string }> = {
  attention_instant: {
    label: 'Отвечать сразу',
    title: 'Без пауз «Внимания»: для проверки',
    on: 'В этом чате она отвечает сразу',
    off: 'В этом чате она отвечает как живой человек',
  },
  no_refusals: {
    label: 'Без отказов',
    title: 'Этому собеседнику она не отказывает: не может сейчас — сделает позже',
    on: 'В этом чате она не отказывает',
    off: 'В этом чате она может отказать, как обычно',
  },
};

/** One switch of the chat header: «Отвечать сразу» (docs/attention.md) or «Без отказов»
 * (docs/writing.md). Shows the new value while it is being saved. */
export function ChatFlagSwitch({ chatId, flag, enabled }: { chatId: number; flag: ChatFlag; enabled: boolean }) {
  const queryClient = useQueryClient();
  const text = FLAGS[flag];
  const toggle = useMutation({
    mutationFn: (next: boolean) => chatsApi.setFlag(chatId, flag, next),
    onSuccess: (_row, next) => {
      toast.success(next ? text.on : text.off);
      void queryClient.invalidateQueries({ queryKey: qk.chat(chatId) });
      void queryClient.invalidateQueries({ queryKey: qk.chatStatus(chatId) });
      void queryClient.invalidateQueries({ queryKey: qk.chatLists });
    },
  });
  const checked = toggle.isPending ? (toggle.variables ?? enabled) : enabled;
  return (
    <label className="flex items-center gap-2 text-sm" title={text.title}>
      <Switch checked={checked} onCheckedChange={(v) => toggle.mutate(v)} disabled={toggle.isPending} />
      {text.label}
    </label>
  );
}
