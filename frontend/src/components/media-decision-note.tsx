import { ClipboardList, TriangleAlert } from 'lucide-react';
import type { MediaDecisionInfo } from '@/lib/models';

export const MEDIA_DECISION_LABEL = 'решение медиа';

/**
 * A service record of the persona's media decision: what was asked (a photo, a video note or
 * nothing), what she decided, the reason she named and the outcome. It is written by the system
 * and is never sent to the client.
 */
export function MediaDecisionNote({ decision }: { decision: MediaDecisionInfo }) {
  return (
    <div data-media-decision={decision.decision} className="flex min-w-0 flex-col gap-1 text-xs">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          <ClipboardList className="size-3" />
          {MEDIA_DECISION_LABEL}
        </span>
        <strong className="font-medium">{decision.headline}</strong>
      </p>
      <ul className="text-muted-foreground [overflow-wrap:anywhere]">
        {decision.lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      {decision.warning && (
        <p role="note" className="flex items-start gap-1 text-warning [overflow-wrap:anywhere]">
          <TriangleAlert className="mt-0.5 size-3.5 flex-none" />
          <span>Предупреждение: {decision.warning}</span>
        </p>
      )}
      <p className="text-[11px] text-muted-foreground">Служебная запись, клиенту не отправляется.</p>
    </div>
  );
}
