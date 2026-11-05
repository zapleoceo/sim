import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ClientStatus } from '../core/models';
import { STATUS_LABELS } from '../core/stages';

@Component({
  selector: 'sim-status-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[attr.data-status]': 'status()' },
  template: `{{ label() }}`,
  styles: `
    :host { display: inline-block; padding: 0 8px; border-radius: 6px; font-size: 12px; line-height: 20px; font-weight: 500; white-space: nowrap; background: var(--sim-surface-2); color: var(--sim-text-muted); }
    :host([data-status='active']) { color: var(--sim-ok); background: color-mix(in srgb, var(--sim-ok) 14%, transparent); }
    :host([data-status='paused']) { color: var(--sim-warn); background: color-mix(in srgb, var(--sim-warn) 14%, transparent); }
    :host([data-status='refused']) { color: var(--sim-danger); background: color-mix(in srgb, var(--sim-danger) 14%, transparent); }
  `,
})
export class StatusBadgeComponent {
  readonly status = input.required<ClientStatus>();

  protected readonly label = computed(() => STATUS_LABELS[this.status()] ?? this.status());
}
