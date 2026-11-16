import type { EventVm } from '@/features/inbox/journal-logic';
import { cn } from '@/lib/utils';
import { voiceNotice } from '@/lib/voice-notice';

/**
 * One line of the communication journal inside the feed: a service record, not a message to the
 * client. Centered and dashed so it never reads as a bubble; it names the type, what changed,
 * who did it and why.
 */
export function JournalLine({ vm }: { vm: EventVm }) {
  const voiceReason = voiceNotice(vm.reason);
  return (
    <div className="my-0.5 flex justify-center">
      <div
        role="note"
        aria-label={`Служебное событие: ${vm.tag}`}
        data-event-kind={vm.event.kind}
        className="flex max-w-[94%] flex-col gap-0.5 rounded-lg border border-dashed bg-background/70 px-3 py-1.5 text-xs sm:max-w-[80%]"
      >
        <span className="flex flex-wrap items-baseline gap-x-1.5">
          <span className="rounded bg-muted px-1.5 py-px text-[11px] font-medium">{vm.tag}</span>
          {vm.change && <span className="min-w-0 font-medium break-words">{vm.change}</span>}
        </span>
        <span className="text-muted-foreground">
          {vm.who} · {vm.time} · <span className={cn(!vm.hasReason && 'italic')}>{voiceReason ?? (vm.hasReason ? `причина: ${vm.reason}` : vm.reason)}</span>
        </span>
        {voiceReason && <details><summary>Подробности</summary>{vm.reason}</details>}
      </div>
    </div>
  );
}
