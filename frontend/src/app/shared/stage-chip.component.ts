import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { stageMeta } from '../core/stages';

@Component({
  selector: 'sim-stage-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[style.--chip-color]': 'meta().color' },
  template: `<span class="dot"></span>{{ meta().label }}`,
  styles: `
    :host {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 24px;
      padding: 0 10px;
      border-radius: 12px;
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
      color: var(--sim-text);
      background: color-mix(in srgb, var(--chip-color) 16%, transparent);
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--chip-color); }
  `,
})
export class StageChipComponent {
  readonly stage = input.required<string>();

  protected readonly meta = computed(() => stageMeta(this.stage()));
}
