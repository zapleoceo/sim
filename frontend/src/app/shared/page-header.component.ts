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
    :host { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--sim-space-3) var(--sim-space-4); }
    .titles { min-width: 0; flex: 1 1 240px; display: flex; flex-direction: column; gap: 2px; }
    .titles p { font-size: 13px; }
    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sim-space-2); }
    .actions:empty { display: none; }
    @media (max-width: 767px) {
      h1 { font-size: 20px; }
      .actions { flex: 1 1 100%; }
      .actions > * { flex: 1 1 auto; }
    }
  `,
})
export class PageHeaderComponent {
  readonly title = input.required<string>();
  readonly subtitle = input('');
}
