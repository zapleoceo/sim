import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'sim-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="titles">
      <h1>{{ title() }}</h1>
      @if (subtitle()) {
        <p class="sim-muted">{{ subtitle() }}</p>
      }
    </div>
    <div class="actions"><ng-content /></div>
  `,
  styles: `
    :host { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--sim-space-3); }
    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sim-space-2); }
  `,
})
export class PageHeaderComponent {
  readonly title = input.required<string>();
  readonly subtitle = input('');
}
