import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Метка «нужен оператор»: ИИ передал диалог человеку или не смог ответить. */
@Component({
  selector: 'sim-needs-operator-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `нужен оператор`,
  styles: `
    :host { display: inline-block; padding: 0 8px; border-radius: 6px; font-size: 12px; line-height: 20px; font-weight: 500; white-space: nowrap; color: var(--sim-danger); background: color-mix(in srgb, var(--sim-danger) 14%, transparent); }
  `,
})
export class NeedsOperatorBadgeComponent {}
