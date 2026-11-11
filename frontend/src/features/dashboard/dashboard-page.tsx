import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { BarChart3, Network } from 'lucide-react';
import { useState } from 'react';
import { ChannelIcon } from '@/components/badges';
import { DataTable } from '@/components/data-table';
import { EmptyState, ErrorState, InlineError } from '@/components/empty-state';
import { FilterBar, FilterField } from '@/components/filter-bar';
import { OptionSelect } from '@/components/option-select';
import { Page, PageHeader } from '@/components/page';
import { RoutePending } from '@/components/route-states';
import { Button } from '@/components/ui/button';
import { Card, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { analyticsApi } from '@/lib/api/analytics';
import { personasApi } from '@/lib/api/personas';
import { qk } from '@/lib/query';
import { DailyLineChart, StageBarChart } from './dashboard-charts';
import {
  PRESETS, buildKpis, channelRows, dailyPoints, periodValid, presetPeriod, stageBars, type ChannelRow, type Period,
} from './dashboard-logic';

const channelColumns: ColumnDef<ChannelRow, unknown>[] = [
  {
    id: 'label',
    header: 'Канал',
    cell: ({ row }) => (
      <span className="flex items-center gap-2">
        <ChannelIcon kind={row.original.kind} size={20} />
        {row.original.label}
      </span>
    ),
  },
  { id: 'contacts', header: 'Контакты', cell: ({ row }) => row.original.contacts, meta: { className: 'text-right tabular' } },
  { id: 'registrations', header: 'Регистрации', cell: ({ row }) => row.original.registrations, meta: { className: 'text-right tabular' } },
  { id: 'payments', header: 'Оплаты', cell: ({ row }) => row.original.payments, meta: { className: 'text-right tabular' } },
];

export function DashboardPage() {
  const [period, setPeriod] = useState<Period>(() => presetPeriod(30));
  const [personaId, setPersonaId] = useState<number | null>(null);
  const valid = periodValid(period);

  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const summary = useQuery({
    queryKey: qk.analytics(period.from, period.to, personaId),
    queryFn: () => analyticsApi.summary(period.from, period.to, personaId),
    enabled: valid,
    placeholderData: keepPreviousData,
  });
  const d = summary.data;

  return (
    <>
      {summary.isFetching && <RoutePending />}
      <Page>
        <PageHeader title="Аналитика" subtitle="Контакты, конверсия и затраты за период">
          <div role="group" aria-label="Быстрый выбор периода" className="flex gap-1">
            {PRESETS.map((days) => (
              <Button key={days} variant="outline" size="sm" onClick={() => setPeriod(presetPeriod(days))}>
                {days} дней
              </Button>
            ))}
          </div>
        </PageHeader>

        <FilterBar
          activeCount={personaId === null ? 0 : 1}
          search={
            <div className="flex flex-wrap items-end gap-2">
              <FilterField label="С">
                <Input
                  type="date"
                  value={period.from}
                  max={period.to || undefined}
                  onChange={(e) => setPeriod((p) => ({ ...p, from: e.target.value }))}
                  className="w-40"
                />
              </FilterField>
              <FilterField label="По">
                <Input
                  type="date"
                  value={period.to}
                  min={period.from || undefined}
                  onChange={(e) => setPeriod((p) => ({ ...p, to: e.target.value }))}
                  className="w-40"
                />
              </FilterField>
            </div>
          }
          filters={
            <FilterField label="Персонаж">
              <OptionSelect
                value={personaId}
                onChange={setPersonaId}
                options={[{ value: null, label: 'Все персонажи' }, ...(personas.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
              />
            </FilterField>
          }
        />
        {!valid && <InlineError>Выберите период: дата «По» не раньше даты «С».</InlineError>}

        {summary.isError && !d ? (
          <ErrorState onRetry={() => void summary.refetch()} />
        ) : !d ? (
          summary.isPending && valid ? (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {Array.from({ length: 8 }, (_, i) => (
                <Skeleton key={i} className="h-24 rounded-xl" />
              ))}
            </div>
          ) : (
            <EmptyState icon={BarChart3} title="Нет данных" hint="Выберите период и персонажа." />
          )
        ) : (
          <>
            <section aria-label="Ключевые показатели" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
              {buildKpis(d).map((k) => (
                <div key={k.label} className="flex min-w-0 flex-col gap-1 rounded-xl border bg-card p-4">
                  <span className="text-xs text-muted-foreground">{k.label}</span>
                  <strong className="text-lg leading-tight font-semibold tabular [overflow-wrap:anywhere] sm:text-xl">{k.value}</strong>
                  {k.hint && <span className="text-xs text-muted-foreground">{k.hint}</span>}
                </div>
              ))}
            </section>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardTitle>Воронка по этапам</CardTitle>
                <StageBarChart data={stageBars(d)} />
              </Card>
              <Card>
                <CardTitle>По дням</CardTitle>
                <DailyLineChart data={dailyPoints(d)} />
              </Card>
            </div>

            <Card className="gap-2">
              <CardTitle>По каналам</CardTitle>
              <DataTable
                columns={channelColumns}
                data={channelRows(d)}
                getRowId={(r) => r.kind}
                caption="Показатели по каналам"
                empty={<EmptyState icon={Network} title="Нет данных по каналам" />}
              />
            </Card>
          </>
        )}
      </Page>
    </>
  );
}
