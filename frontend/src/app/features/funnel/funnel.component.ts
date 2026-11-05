import { CdkDrag, CdkDragRelease, CdkDragDrop, CdkDropList, CdkDropListGroup } from '@angular/cdk/drag-drop';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatSelectModule } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, startWith, switchMap } from 'rxjs';
import { ClientsApi } from '../../core/api/clients.api';
import { PersonasApi } from '../../core/api/personas.api';
import { checkStageDrop } from '../../core/funnel-rules';
import { ClientMini, ClientStatus, FunnelResponse, Persona, Stage } from '../../core/models';
import { STAGES, STATUSES, STATUS_LABELS, stageMeta } from '../../core/stages';
import { ToastService } from '../../core/toast.service';
import { AvatarComponent } from '../../shared/avatar.component';
import { FilterBarComponent, FiltersTemplateDirective } from '../../shared/filter-bar.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

interface Column { stage: Stage; label: string; color: string; locked: boolean; count: number; clients: ClientMini[] }

@Component({
  selector: 'sim-funnel',
  imports: [
    ReactiveFormsModule, RouterLink, CdkDropListGroup, CdkDropList, CdkDrag, MatButtonModule, MatFormFieldModule,
    MatIconModule, MatMenuModule, MatSelectModule, FilterBarComponent, FiltersTemplateDirective, PageHeaderComponent, AvatarComponent, StatusBadgeComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './funnel.component.html',
  styleUrl: './funnel.component.scss',
})
export class FunnelComponent {
  private readonly api = inject(ClientsApi);
  private readonly personasApi = inject(PersonasApi);
  private readonly toast = inject(ToastService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly persona = new FormControl<number | null>(null);
  protected readonly statusOrder = STATUSES;
  protected readonly statusLabels = STATUS_LABELS;
  protected readonly moveTargets = STAGES.filter((s) => s.aiReachable);

  protected readonly personas = signal<Persona[]>([]);
  protected readonly columns = signal<Column[]>([]);
  protected readonly statuses = signal<Record<ClientStatus, number> | null>(null);
  protected readonly loading = signal(true);

  /** Колонки, куда нельзя бросить карточку (регистрация/оплата — только партнёр). */
  protected readonly canEnter = (_drag: CdkDrag<ClientMini>, drop: CdkDropList<Stage>): boolean =>
    stageMeta(drop.data).aiReachable;

  constructor() {
    this.personasApi
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((p) => this.personas.set(p));

    this.persona.valueChanges
      .pipe(
        startWith(null),
        switchMap(() => {
          this.loading.set(true);
          return this.api.funnel(this.persona.value).pipe(catchError(() => EMPTY));
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((f) => this.apply(f));
  }

  protected onDrop(ev: CdkDragDrop<Stage, Stage, ClientMini>): void {
    const from = ev.previousContainer.data;
    const to = ev.container.data;
    if (from === to) return;
    this.move(ev.item.data, from, to);
  }

  /** Пользователь отпустил карточку над запрещённой колонкой: объясняем, почему она вернулась. */
  protected onRelease(ev: CdkDragRelease<ClientMini>): void {
    const point = this.pointer(ev.event);
    if (!point) return;
    const stage = document.elementFromPoint(point.x, point.y)?.closest<HTMLElement>('[data-stage]')?.dataset['stage'] as Stage | undefined;
    if (!stage) return;
    const verdict = checkStageDrop(ev.source.data.stage, stage);
    if (!verdict.ok && verdict.reason) this.toast.info(verdict.reason);
  }

  protected move(client: ClientMini, from: Stage, to: Stage): void {
    const verdict = checkStageDrop(from, to);
    if (!verdict.ok) {
      if (verdict.reason) this.toast.info(verdict.reason);
      return;
    }
    const snapshot = this.columns();
    this.columns.set(
      snapshot.map((c) => {
        if (c.stage === from) return { ...c, count: Math.max(0, c.count - 1), clients: c.clients.filter((x) => x.id !== client.id) };
        if (c.stage === to) return { ...c, count: c.count + 1, clients: [{ ...client, stage: to }, ...c.clients] };
        return c;
      }),
    );
    this.api
      .changeStage(client.id, { stage: to })
      .pipe(
        catchError(() => {
          this.columns.set(snapshot); // откат: сервер отклонил перенос (toast покажет интерцептор)
          return EMPTY;
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }

  private apply(f: FunnelResponse): void {
    const byStage = new Map(f.stages.map((s) => [s.stage, s]));
    this.columns.set(
      STAGES.map((s) => {
        const src = byStage.get(s.id);
        return { stage: s.id, label: s.label, color: s.color, locked: !s.aiReachable, count: src?.count ?? 0, clients: src?.clients ?? [] };
      }),
    );
    this.statuses.set(f.statuses);
    this.loading.set(false);
  }

  private pointer(e: MouseEvent | TouchEvent): { x: number; y: number } | null {
    if ('changedTouches' in e) {
      const t = e.changedTouches[0];
      return t ? { x: t.clientX, y: t.clientY } : null;
    }
    return { x: e.clientX, y: e.clientY };
  }
}
