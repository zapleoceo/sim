import { useQuery } from '@tanstack/react-query';
import { Card, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { analyticsApi } from '@/lib/api/analytics';
import { formatMoney } from '@/lib/format';
import { qk } from '@/lib/query';
import { capLines, capShare, spendRows } from './spend-logic';

const usd = (v: number): string => formatMoney(v, 'USD', v > 0 && v < 1 ? 4 : 2);

/**
 * Money spent by the whole SIM project for the period, straight from Broker (`/v1/usage`):
 * nothing is re-counted here. Subscription models are flat-rate and never appear as cost.
 */
export function SpendCard({ from, to, enabled }: { from: string; to: string; enabled: boolean }) {
  const q = useQuery({ queryKey: qk.spend(from, to), queryFn: () => analyticsApi.spend(from, to), enabled });
  const s = q.data;

  return (
    <Card className="gap-3">
      <CardTitle>Расходы</CardTitle>
      {q.isPending && enabled && <Skeleton className="h-28 w-full" />}
      {q.isError && <p className="text-sm text-destructive">Не удалось получить расходы.</p>}
      {s && !s.available && (
        <p className="text-sm text-muted-foreground">Broker не отдал расходы: {s.error}</p>
      )}
      {s?.available && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <strong className="text-2xl font-semibold tabular" aria-label="Потрачено за период">{usd(s.total.cost_usd)}</strong>
            <span className="text-sm text-muted-foreground">
              за период, {s.total.calls} платных и бесплатных запросов через Broker
            </span>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {capLines(s).map((line) => {
              const share = capShare(line);
              return (
                <div key={line.label} className="flex flex-col gap-1.5 rounded-lg border p-3 text-sm">
                  <span className="text-muted-foreground">{line.label}</span>
                  <span className="tabular">
                    {usd(line.used)} {line.cap === null ? 'без лимита' : `из ${usd(line.cap)}`}
                  </span>
                  {share !== null && (
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="meter" aria-label={`${line.label}: доля лимита`}
                      aria-valuemin={0} aria-valuemax={1} aria-valuenow={share}>
                      <div className={share >= 0.9 ? 'h-full bg-destructive' : 'h-full bg-primary'} style={{ width: `${share * 100}%` }} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {spendRows(s).length > 0 && (
            <table className="w-full text-sm">
              <caption className="sr-only">Расходы по видам запросов</caption>
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 text-left font-normal">На что</th>
                  <th className="py-1 text-right font-normal">Запросов</th>
                  <th className="py-1 text-right font-normal">Потрачено</th>
                </tr>
              </thead>
              <tbody>
                {spendRows(s).map((r) => (
                  <tr key={r.capability} className="border-t">
                    <td className="py-1.5">{r.label}</td>
                    <td className="py-1.5 text-right tabular">{r.calls}</td>
                    <td className="py-1.5 text-right tabular">{usd(r.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-muted-foreground">
            Данные Broker по всему проекту SIM, фильтр персонажа сюда не применяется. Подписки (Claude, Codex)
            оплачиваются фиксированно и в сумму не входят.
          </p>
        </div>
      )}
    </Card>
  );
}
