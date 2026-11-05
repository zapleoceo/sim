import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { GuardInfo, GuardVerdict } from '../core/models';

export const GUARD_LABELS: Record<GuardVerdict, string> = {
  ok: 'Проверено контролером',
  rewritten: 'Исправлено контролером',
  blocked: 'Заблокировано контролером',
};

const GUARD_ICONS: Record<GuardVerdict, string> = { ok: 'verified_user', rewritten: 'shield', blocked: 'gpp_bad' };

/** Щит контролера: вердикт, нарушения и исходный черновик (раскрывается). */
@Component({
  selector: 'sim-guard-badge',
  imports: [MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (guard(); as g) {
      <details class="guard" [attr.data-verdict]="g.verdict">
        <summary [title]="title()">
          <mat-icon class="material-icons-outlined">{{ icon() }}</mat-icon>
          <span>{{ label() }}</span>
        </summary>
        <div class="body">
          @if (g.violations.length) {
            <ul>
              @for (v of g.violations; track $index) {
                <li>{{ v }}</li>
              }
            </ul>
          }
          @if (g.original_text) {
            <span class="cap">{{ g.verdict === 'blocked' ? 'Заблокированный черновик' : 'Исходный черновик' }}</span>
            <p class="draft">{{ g.original_text }}</p>
          }
          @if (g.model) {
            <span class="cap">Контролер: {{ g.model }}</span>
          }
        </div>
      </details>
    }
  `,
  styles: `
    .guard { font-size: 11px; color: var(--sim-text-muted); margin-top: 2px; }
    summary { display: inline-flex; align-items: center; gap: 2px; cursor: pointer; list-style: none; }
    summary::-webkit-details-marker { display: none; }
    mat-icon { font-size: 14px; width: 14px; height: 14px; }
    [data-verdict='ok'] summary { color: var(--sim-ok); }
    [data-verdict='rewritten'] summary { color: var(--sim-accent); }
    [data-verdict='blocked'] summary { color: var(--sim-danger); font-weight: 600; }
    .body { margin-top: 4px; padding: 6px 8px; border-radius: 8px; background: var(--sim-surface-2); color: var(--sim-text); }
    ul { margin: 0 0 4px; padding-left: 16px; }
    .cap { display: block; color: var(--sim-text-muted); }
    .draft { margin: 2px 0 4px; white-space: pre-wrap; overflow-wrap: anywhere; }
  `,
})
export class GuardBadgeComponent {
  readonly guard = input<GuardInfo | null | undefined>(null);

  protected readonly label = computed(() => {
    const g = this.guard();
    return g ? GUARD_LABELS[g.verdict] : '';
  });
  protected readonly icon = computed(() => {
    const g = this.guard();
    return g ? GUARD_ICONS[g.verdict] : '';
  });
  protected readonly title = computed(() => this.guard()?.violations.join('; ') || this.label());
}
