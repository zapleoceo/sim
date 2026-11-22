import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { MutationSwitch, shownValue } from '@/components/mutation-switch';
import { DropdownMenuCheckboxItem } from '@/components/ui/dropdown-menu';
import { chatsApi, invalidateChat, type ChatFlag } from './api';

/** The chat header's own switches, one definition each (docs/frontend.md). */
export const CHAT_FLAGS: Record<ChatFlag, { label: string; title: string; on: string; off: string }> = {
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
  calls_out: {
    label: 'Звонит сама',
    title: 'Марина может сама позвонить в этом чате: когда договорились созвониться или есть повод. Не чаще раза в 3 часа и не больше 3 раз в день, не ночью.',
    on: 'В этом чате она может позвонить сама',
    off: 'В этом чате она сама не звонит',
  },
};

/** One flag of one chat: the request, the toast and the refresh, for the switch and the phone menu. */
function useChatFlag(chatId: number, flag: ChatFlag) {
  const queryClient = useQueryClient();
  const text = CHAT_FLAGS[flag];
  const toggle = useMutation({
    mutationFn: (next: boolean) => chatsApi.setFlag(chatId, flag, next),
    onSuccess: (_row, next) => {
      toast.success(next ? text.on : text.off);
      invalidateChat(queryClient, chatId);
    },
  });
  return { text, toggle };
}

/** One switch of the chat header: «Отвечать сразу» (docs/attention.md), «Без отказов»
 * (docs/writing.md) or «Звонит сама» (docs/calls.md). Shows the new value while it is being saved. */
export function ChatFlagSwitch({ chatId, flag, enabled }: { chatId: number; flag: ChatFlag; enabled: boolean }) {
  const { text, toggle } = useChatFlag(chatId, flag);
  return (
    <MutationSwitch
      checked={enabled}
      pending={toggle.isPending}
      pendingValue={toggle.variables}
      onCheckedChange={(v) => toggle.mutate(v)}
      label={text.label}
      title={text.title}
    />
  );
}

/** The same flag as a checkbox item of the header's menu on a phone. */
export function ChatFlagMenuItem({ chatId, flag, enabled }: { chatId: number; flag: ChatFlag; enabled: boolean }) {
  const { text, toggle } = useChatFlag(chatId, flag);
  return (
    <DropdownMenuCheckboxItem
      checked={shownValue(enabled, toggle.isPending, toggle.variables)}
      disabled={toggle.isPending}
      onCheckedChange={(v) => toggle.mutate(v)}
    >
      {text.label}
    </DropdownMenuCheckboxItem>
  );
}
