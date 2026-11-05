import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export interface BarDatum { label: string; value: number; color: string }
export interface LineSeries { name: string; color: string; values: number[] }

/** Горизонтальные столбцы (воронка по этапам) на чистом CSS. */
@Component({
  selector: 'sim-bar-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ul class="bars" role="list">
      @for (b of rows(); track b.label) {
        <li>
          <span class="label">{{ b.label }}</span>
          <span class="track" aria-hidden="true"><span class="fill" [style.width.%]="b.pct" [style.background]="b.color"></span></span>
          <span class="value">{{ b.value }}</span>
        </li>
      }
    </ul>
  `,
  styles: `
    .bars { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sim-space-3); }
    li { display: grid; grid-template-columns: minmax(96px, 150px) 1fr 48px; align-items: center; gap: var(--sim-space-3); }
    .label { color: var(--sim-text-muted); font-size: 13px; }
    .track { height: 10px; border-radius: 5px; background: var(--sim-surface-2); overflow: hidden; }
    .fill { display: block; height: 100%; border-radius: 5px; min-width: 2px; transition: width 0.3s; }
    .value { text-align: right; font-variant-numeric: tabular-nums; font-weight: 500; }
  `,
})
export class BarChartComponent {
  readonly data = input.required<BarDatum[]>();

  protected readonly rows = computed(() => {
    const max = Math.max(1, ...this.data().map((d) => d.value));
    return this.data().map((d) => ({ ...d, pct: (d.value / max) * 100 }));
  });
}

const W = 640;
const H = 220;
const PAD = { l: 36, r: 8, t: 8, b: 24 };

@Component({
  selector: 'sim-line-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="legend">
      @for (s of series(); track s.name) {
        <span><i [style.background]="s.color"></i>{{ s.name }}</span>
      }
    </div>
    <svg [attr.viewBox]="'0 0 ' + w + ' ' + h" role="img" [attr.aria-label]="ariaLabel()">
      @for (t of yTicks(); track t.y) {
        <line [attr.x1]="pad.l" [attr.x2]="w - pad.r" [attr.y1]="t.y" [attr.y2]="t.y" class="grid" />
        <text [attr.x]="pad.l - 6" [attr.y]="t.y + 4" class="axis" text-anchor="end">{{ t.v }}</text>
      }
      @for (x of xTicks(); track x.x) {
        <text [attr.x]="x.x" [attr.y]="h - 6" class="axis" text-anchor="middle">{{ x.label }}</text>
      }
      @for (p of paths(); track p.name) {
        <path [attr.d]="p.d" [attr.stroke]="p.color" fill="none" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />
      }
    </svg>
  `,
  styles: `
    :host { display: block; }
    svg { width: 100%; height: auto; display: block; }
    .grid { stroke: var(--sim-border); stroke-width: 1; vector-effect: non-scaling-stroke; }
    .axis { fill: var(--sim-text-muted); font-size: 11px; }
    .legend { display: flex; flex-wrap: wrap; gap: var(--sim-space-4); margin-bottom: var(--sim-space-2); color: var(--sim-text-muted); font-size: 12px; }
    .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; }
  `,
})
export class LineChartComponent {
  readonly labels = input.required<string[]>();
  readonly series = input.required<LineSeries[]>();

  protected readonly w = W;
  protected readonly h = H;
  protected readonly pad = PAD;

  protected readonly max = computed(() => Math.max(1, ...this.series().flatMap((s) => s.values)));
  protected readonly ariaLabel = computed(() => `График: ${this.series().map((s) => s.name).join(', ')}`);

  protected readonly yTicks = computed(() => {
    const max = this.max();
    return [0, 0.5, 1].map((f) => ({ v: Math.round(max * f), y: PAD.t + (1 - f) * (H - PAD.t - PAD.b) }));
  });

  protected readonly xTicks = computed(() => {
    const n = this.labels().length;
    if (n === 0) return [];
    const step = Math.max(1, Math.ceil(n / 6));
    return this.labels()
      .map((label, i) => ({ label, x: this.x(i, n) }))
      .filter((_, i) => i % step === 0);
  });

  protected readonly paths = computed(() => {
    const max = this.max();
    return this.series().map((s) => ({
      name: s.name,
      color: s.color,
      d: s.values
        .map((v, i) => {
          const x = this.x(i, s.values.length);
          const y = PAD.t + (1 - v / max) * (H - PAD.t - PAD.b);
          return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(' '),
    }));
  });

  private x(i: number, n: number): number {
    return n <= 1 ? (W + PAD.l - PAD.r) / 2 : PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r);
  }
}
