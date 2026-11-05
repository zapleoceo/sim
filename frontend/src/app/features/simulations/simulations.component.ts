import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { EMPTY, catchError } from 'rxjs';
import { SimulationsApi } from '../../core/api/simulations.api';
import { SimBranchDetail, SimBranchRow, SimMessage } from '../../core/models';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { formatDateTime, formatTime } from '../../shared/format';
import { GuardBadgeComponent } from '../../shared/guard-badge.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { StageChipComponent } from '../../shared/stage-chip.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

/** Тестовые переписки админов с персонажами из бота (только просмотр). */
@Component({
  selector: 'sim-simulations',
  imports: [
    MatButtonModule, PageHeaderComponent, EmptyStateComponent, GuardBadgeComponent, StageChipComponent,
    StatusBadgeComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page">
      <sim-page-header
        title="Симуляции"
        subtitle="Тестовые диалоги админов с персонажами в боте. Не попадают в диалоги, клиентов и аналитику."
      />

      <div class="layout" [class.reading]="detail() !== null">
        <div class="list">
          @for (r of rows(); track r.b.id) {
            <button type="button" class="sim-card item" [class.active]="detail()?.id === r.b.id" (click)="open(r.b)">
              <span class="row">
                <strong>{{ r.b.persona.name }}</strong>
                <sim-stage-chip [stage]="r.b.stage" />
              </span>
              <span class="sim-muted">{{ r.b.admin.display_name }} · {{ r.b.message_count }} сообщ. · {{ r.last }}</span>
              @if (r.b.closed_at) {
                <span class="sim-muted">закрыта</span>
              }
            </button>
          } @empty {
            @if (loaded()) {
              <sim-empty-state icon="science" title="Симуляций пока нет" hint="Админ начинает их командой /persona в боте входа." />
            }
          }
        </div>

        @if (view(); as v) {
          @let d = v.detail;
          <section class="sim-card transcript" aria-label="Переписка симуляции">
            <header>
              <div class="sim-grow">
                <h2>{{ d.title }}</h2>
                <span class="sim-muted">{{ d.admin.display_name }} · создана {{ v.created }}</span>
              </div>
              <sim-status-badge [status]="d.status" />
              <button mat-button type="button" (click)="detail.set(null)">Закрыть</button>
            </header>
            @if (v.needs || d.next_step) {
              <p class="sim-muted state">
                @if (v.needs) {
                  Потребности: {{ v.needs }}.
                }
                @if (d.next_step) {
                  Следующий шаг: {{ d.next_step }}
                }
              </p>
            }
            <div class="msgs">
              @for (m of v.messages; track m.m.id) {
                <div class="msg" [attr.data-role]="m.m.role">
                  <p class="text">{{ m.m.text }}</p>
                  <span class="meta sim-muted">
                    {{ m.m.role === 'client' ? 'клиент' : 'персонаж' }} · {{ m.time }}
                    @if (m.m.model) {
                      · {{ m.m.model }}
                    }
                  </span>
                  <sim-guard-badge [guard]="m.m.guard" />
                </div>
              }
            </div>
          </section>
        }
      </div>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: minmax(280px, 360px) 1fr; gap: var(--sim-space-4); align-items: start; }
    .layout:not(.reading) { grid-template-columns: 1fr; }
    .list { display: flex; flex-direction: column; gap: var(--sim-space-2); }
    .item { display: flex; flex-direction: column; gap: 4px; text-align: left; cursor: pointer; font: inherit; color: inherit; }
    .item.active { outline: 2px solid var(--sim-accent); }
    .row { display: flex; align-items: center; justify-content: space-between; gap: var(--sim-space-2); }
    .transcript { display: flex; flex-direction: column; gap: var(--sim-space-3); }
    header { display: flex; align-items: center; gap: var(--sim-space-3); }
    h2 { margin: 0; }
    .state { margin: 0; }
    .msgs { display: flex; flex-direction: column; gap: var(--sim-space-2); }
    .msg { max-width: 80%; padding: 8px 12px; border-radius: var(--sim-radius); background: var(--sim-bubble-client); align-self: flex-start; }
    .msg[data-role='ai'] { align-self: flex-end; background: var(--sim-bubble-ai); }
    .text { margin: 0 0 4px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .meta { font-size: 11px; }
    @media (max-width: 800px) {
      .layout.reading { grid-template-columns: 1fr; }
      .layout.reading .list { display: none; }
    }
  `,
})
export class SimulationsComponent {
  private readonly api = inject(SimulationsApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly branches = signal<SimBranchRow[]>([]);
  protected readonly detail = signal<SimBranchDetail | null>(null);
  protected readonly loaded = signal(false);

  protected readonly rows = computed(() =>
    this.branches().map((b) => ({ b, last: formatDateTime(b.last_active_at) })),
  );
  protected readonly view = computed(() => {
    const detail = this.detail();
    if (!detail) return null;
    return {
      detail,
      created: formatDateTime(detail.created_at),
      needs: detail.needs.join('; '),
      messages: detail.messages.map((m: SimMessage) => ({ m, time: formatTime(m.created_at) })),
    };
  });

  constructor() {
    this.api
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((r) => {
        this.branches.set(r.items);
        this.loaded.set(true);
      });
  }

  protected open(branch: SimBranchRow): void {
    this.api
      .get(branch.id)
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((d) => this.detail.set(d));
  }
}
