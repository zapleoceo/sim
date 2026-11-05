import { DecimalPipe, PercentPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatDatepickerModule } from '@angular/material/datepicker';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { EMPTY, catchError, debounceTime, filter, startWith, switchMap, tap } from 'rxjs';
import { AnalyticsApi } from '../../core/api/analytics.api';
import { PersonasApi } from '../../core/api/personas.api';
import { AnalyticsSummary, Persona } from '../../core/models';
import { CHANNEL_LABELS, STAGES } from '../../core/stages';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { BarChartComponent, BarDatum, LineChartComponent, LineSeries } from './charts.component';

interface Kpi { label: string; value: string; hint?: string }

export function toIsoDate(d: Date): string {
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function daysAgo(n: number): Date {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
}

@Component({
  selector: 'sim-dashboard',
  imports: [
    ReactiveFormsModule, MatDatepickerModule, MatFormFieldModule, MatSelectModule, MatIconModule, MatButtonModule,
    PageHeaderComponent, EmptyStateComponent, BarChartComponent, LineChartComponent,
    DecimalPipe, PercentPipe,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent {
  private readonly analytics = inject(AnalyticsApi);
  private readonly personasApi = inject(PersonasApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly filters = new FormGroup({
    range: new FormGroup({
      start: new FormControl<Date | null>(daysAgo(29)),
      end: new FormControl<Date | null>(new Date()),
    }),
    persona_id: new FormControl<number | null>(null),
  });

  protected readonly personas = signal<Persona[]>([]);
  protected readonly data = signal<AnalyticsSummary | null>(null);
  protected readonly loading = signal(false);

  protected readonly kpis = computed<Kpi[]>(() => {
    const d = this.data();
    if (!d) return [];
    return [
      { label: 'Контакты', value: String(d.contacts), hint: `новых: ${d.new_contacts}, ответили: ${d.replied}` },
      { label: 'Регистрации', value: String(d.registrations) },
      { label: 'Оплаты', value: String(d.payments) },
      { label: 'Выручка', value: this.money(d.revenue) },
      { label: 'Выплаты партнёра', value: this.money(d.partner_payouts) },
      { label: 'Расход на LLM', value: `$${d.llm.cost_usd.toFixed(2)}`, hint: `${d.llm.calls} вызовов` },
      {
        label: 'Контролер ответов',
        value: String(d.guard.ok + d.guard.rewritten + d.guard.blocked),
        hint: `ок: ${d.guard.ok}, исправлено: ${d.guard.rewritten}, заблокировано: ${d.guard.blocked}`,
      },
    ];
  });

  protected readonly stageBars = computed<BarDatum[]>(() => {
    const by = this.data()?.by_stage ?? {};
    return STAGES.map((s) => ({ label: s.label, value: by[s.id] ?? 0, color: s.color }));
  });

  protected readonly dailyLabels = computed(() => (this.data()?.daily ?? []).map((x) => x.date.slice(5)));
  protected readonly dailySeries = computed<LineSeries[]>(() => {
    const daily = this.data()?.daily ?? [];
    return [
      { name: 'Контакты', color: 'var(--sim-stage-2)', values: daily.map((x) => x.contacts) },
      { name: 'Регистрации', color: 'var(--sim-stage-4)', values: daily.map((x) => x.registrations) },
      { name: 'Оплаты', color: 'var(--sim-stage-7)', values: daily.map((x) => x.payments) },
    ];
  });

  protected readonly channelRows = computed(() =>
    (this.data()?.by_channel ?? []).map((c) => ({ ...c, label: CHANNEL_LABELS[c.kind] ?? c.kind })),
  );

  constructor() {
    this.personasApi
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((p) => this.personas.set(p));

    this.filters.valueChanges
      .pipe(
        startWith(null),
        debounceTime(150),
        filter(() => !!this.filters.controls.range.controls.start.value && !!this.filters.controls.range.controls.end.value),
        tap(() => this.loading.set(true)),
        switchMap(() => {
          const { range, persona_id } = this.filters.getRawValue();
          return this.analytics
            .summary(toIsoDate(range.start as Date), toIsoDate(range.end as Date), persona_id)
            .pipe(
              catchError(() => {
                this.loading.set(false);
                return EMPTY;
              }),
            );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((d) => {
        this.data.set(d);
        this.loading.set(false);
      });
  }

  protected setPreset(days: number): void {
    this.filters.controls.range.setValue({ start: daysAgo(days - 1), end: new Date() });
  }

  private money(v: number): string {
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(v);
  }
}
