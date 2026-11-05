import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, input } from '@angular/core';
import { BarController, BarElement, CategoryScale, Chart, ChartConfiguration, Legend, LineController, LineElement, LinearScale, PointElement, Tooltip } from 'chart.js';
import { BaseChartDirective } from 'ng2-charts';
import { injectMediaQuery } from '../../shared/breakpoints';

// Регистрируем только нужное (tree-shaking): без registerables.
Chart.register(BarController, BarElement, LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend);

export interface BarDatum { label: string; value: number; color: string }
export interface LineSeries { name: string; color: string; values: number[] }

const DARK_QUERY = '(prefers-color-scheme: dark)';

/**
 * Приводит любой CSS-цвет (в т.ч. `var(--sim-…)` с light-dark()) к вычисленному rgb:
 * canvas не понимает ни var(), ни light-dark(), а кастомные свойства отдают сырой текст.
 */
function resolveColor(host: HTMLElement, value: string): string {
  const probe = document.createElement('span');
  probe.style.color = value;
  probe.style.display = 'none';
  host.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  return resolved;
}

/** Палитра графика из токенов; пересчитывается при смене светлой/тёмной схемы. */
function useChartTheme(host: ElementRef<HTMLElement>): () => { text: string; grid: string; resolve: (v: string) => string } {
  const dark = injectMediaQuery(DARK_QUERY);
  return () => {
    dark(); // зависимость: схема сменилась -> перерисовать
    const el = host.nativeElement;
    return {
      text: resolveColor(el, 'var(--sim-text-muted)'),
      grid: resolveColor(el, 'var(--sim-border)'),
      resolve: (v: string) => resolveColor(el, v),
    };
  };
}

const FONT = { family: 'Inter, system-ui, sans-serif', size: 12 };

/** Воронка по этапам: горизонтальные столбцы. */
@Component({
  selector: 'sim-bar-chart',
  imports: [BaseChartDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap">
      <canvas baseChart type="bar" [data]="chart().data" [options]="chart().options" role="img" [attr.aria-label]="ariaLabel()"></canvas>
    </div>
  `,
  styles: `
    :host { display: block; }
    .wrap { position: relative; height: 280px; }
  `,
})
export class BarChartComponent {
  readonly data = input.required<BarDatum[]>();

  private readonly theme = useChartTheme(inject<ElementRef<HTMLElement>>(ElementRef));

  protected readonly ariaLabel = computed(() => 'Воронка: ' + this.data().map((d) => `${d.label} — ${d.value}`).join(', '));

  protected readonly chart = computed<ChartConfiguration<'bar'>>(() => {
    const t = this.theme();
    const rows = this.data();
    return {
      type: 'bar',
      data: {
        labels: rows.map((r) => r.label),
        datasets: [
          {
            data: rows.map((r) => r.value),
            backgroundColor: rows.map((r) => t.resolve(r.color)),
            borderRadius: 4,
            maxBarThickness: 22,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.parsed.x}` } } },
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0, color: t.text, font: FONT }, grid: { color: t.grid } },
          y: { ticks: { color: t.text, font: FONT }, grid: { display: false } },
        },
      },
    };
  });
}

/** Динамика по дням: линии с подсказками. */
@Component({
  selector: 'sim-line-chart',
  imports: [BaseChartDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap">
      <canvas baseChart type="line" [data]="chart().data" [options]="chart().options" role="img" [attr.aria-label]="ariaLabel()"></canvas>
    </div>
  `,
  styles: `
    :host { display: block; }
    .wrap { position: relative; height: 280px; }
  `,
})
export class LineChartComponent {
  readonly labels = input.required<string[]>();
  readonly series = input.required<LineSeries[]>();

  private readonly theme = useChartTheme(inject<ElementRef<HTMLElement>>(ElementRef));

  protected readonly ariaLabel = computed(() => `График по дням: ${this.series().map((s) => s.name).join(', ')}`);

  protected readonly chart = computed<ChartConfiguration<'line'>>(() => {
    const t = this.theme();
    return {
      type: 'line',
      data: {
        labels: this.labels(),
        datasets: this.series().map((s) => {
          const color = t.resolve(s.color);
          return {
            label: s.name,
            data: s.values,
            borderColor: color,
            backgroundColor: color,
            borderWidth: 2,
            cubicInterpolationMode: 'monotone' as const,
            pointRadius: 0,
            pointHoverRadius: 4,
          };
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'top', align: 'start', labels: { color: t.text, font: FONT, usePointStyle: true, boxWidth: 8 } },
        },
        scales: {
          x: { ticks: { color: t.text, font: FONT, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0, color: t.text, font: FONT }, grid: { color: t.grid } },
        },
      },
    };
  });
}
