import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'sim-empty-state',
  imports: [MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <mat-icon class="material-icons-outlined">{{ icon() }}</mat-icon>
    <h3>{{ title() }}</h3>
    @if (hint()) {
      <p class="sim-muted">{{ hint() }}</p>
    }
    <ng-content />
  `,
  styles: `
    :host { display: flex; flex-direction: column; align-items: center; gap: var(--sim-space-2); padding: var(--sim-space-7) var(--sim-space-4); text-align: center; color: var(--sim-text-muted); }
    mat-icon { font-size: 40px; width: 40px; height: 40px; opacity: 0.6; }
    h3 { color: var(--sim-text); }
  `,
})
export class EmptyStateComponent {
  readonly icon = input('inbox');
  readonly title = input.required<string>();
  readonly hint = input('');
}
