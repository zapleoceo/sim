import { ShieldAlert, ShieldCheck, ShieldHalf } from 'lucide-react';
import type { GuardInfo, GuardVerdict } from '@/lib/models';
import { cn } from '@/lib/utils';

export const GUARD_LABELS: Record<GuardVerdict, string> = {
  ok: 'Проверено контролером',
  rewritten: 'Исправлено контролером',
  blocked: 'Заблокировано контролером',
};

const GUARD_STYLE: Record<GuardVerdict, { icon: typeof ShieldCheck; className: string }> = {
  ok: { icon: ShieldCheck, className: 'text-success' },
  rewritten: { icon: ShieldHalf, className: 'text-primary' },
  blocked: { icon: ShieldAlert, className: 'text-destructive font-semibold' },
};

/** Guard shield: verdict, violations and the original draft (expandable). */
export function GuardBadge({ guard }: { guard: GuardInfo | null | undefined }) {
  if (!guard) return null;
  const style = GUARD_STYLE[guard.verdict];
  const Icon = style.icon;
  const label = GUARD_LABELS[guard.verdict];
  return (
    <details className="group text-[11px] text-muted-foreground">
      <summary
        title={guard.violations.join('; ') || label}
        className={cn('inline-flex cursor-pointer list-none items-center gap-1 [&::-webkit-details-marker]:hidden', style.className)}
      >
        <Icon className="size-3.5" />
        <span>{label}</span>
      </summary>
      <div className="mt-1 space-y-1 rounded-md bg-muted px-2 py-1.5 text-foreground">
        {guard.violations.length > 0 && (
          <ul className="list-disc pl-4">
            {guard.violations.map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        )}
        {guard.original_text && (
          <div>
            <span className="block text-muted-foreground">
              {guard.verdict === 'blocked' ? 'Заблокированный черновик' : 'Исходный черновик'}
            </span>
            <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{guard.original_text}</p>
          </div>
        )}
        {guard.model && <span className="block text-muted-foreground">Контролер: {guard.model}</span>}
      </div>
    </details>
  );
}
