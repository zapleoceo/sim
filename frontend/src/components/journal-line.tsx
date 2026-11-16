import type { EventVm } from '@/features/inbox/journal-logic';
import { voiceNotice } from '@/lib/voice-notice';
import { formatZonedDateTime } from '@/lib/format';

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
          {vm.who} · <time dateTime={vm.event.created_at} title={`${formatZonedDateTime(vm.event.created_at)} (ваше время)`}>{vm.time}</time>
        </span>
        {vm.event.kind === 'notes' && <span className="font-medium">Внутренняя заметка · клиенту не отправляется</span>}
        {vm.steps.length > 0 ? (
          <details><summary className="cursor-pointer">Все шаги ({vm.steps.length})</summary>
            <ol aria-label="Шаги звонка" className="mt-1 flex flex-col gap-0.5 font-mono text-[11px]">
              {vm.steps.map((step, i) => <li key={i} className="break-words">{step}</li>)}
            </ol>
          </details>
        ) : vm.reason && (
          <details><summary className="cursor-pointer">Подробности события</summary>
            <span>{voiceReason ?? `Причина: ${vm.reason}`}</span>
            {voiceReason && <p>{vm.reason}</p>}
          </details>
        )}
      </div>
    </div>
  );
}
