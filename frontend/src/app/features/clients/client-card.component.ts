import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, Injector, afterNextRender, computed, inject, input, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, switchMap, tap } from 'rxjs';
import { ActivityFeedComponent } from '../activity/activity-feed.component';
import { HttpErrorResponse } from '@angular/common/http';
import { ClientsApi } from '../../core/api/clients.api';
import { ClientCard, ClientStatus, FunnelEvent, PartnerEvent, Stage } from '../../core/models';
import { PARTNER_ONLY_HINT, STAGES, STATUSES, STATUS_LABELS, stageMeta } from '../../core/stages';
import { ToastService } from '../../core/toast.service';
import { AvatarComponent } from '../../shared/avatar.component';
import { ChannelIconComponent } from '../../shared/channel-icon.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { DatetimeFieldComponent } from '../../shared/datetime-field.component';
import { formatDateTime, fromDate, toDate } from '../../shared/format';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

interface TimelineItem { at: string; icon: string; title: string; detail: string; partner: boolean }

export function buildTimeline(events: FunnelEvent[], partner: PartnerEvent[]): TimelineItem[] {
  const funnel = events.map<TimelineItem>((e) => ({
    at: e.created_at,
    icon: 'swap_horiz',
    title: e.to_stage
      ? `Этап: ${e.from_stage ? stageMeta(e.from_stage).label + ' → ' : ''}${stageMeta(e.to_stage).label}`
      : (e.type ?? 'Событие воронки'),
    detail: [e.actor, e.reason].filter(Boolean).join(' · '),
    partner: false,
  }));
  const fromPartner = partner.map<TimelineItem>((e) => ({
    at: e.occurred_at,
    icon: e.event === 'payment' ? 'payments' : 'how_to_reg',
    title: e.event === 'payment' ? 'Оплата у партнёра' : e.event === 'registration' ? 'Регистрация у партнёра' : e.event,
    detail: [e.amount != null ? `${e.amount} ${e.currency ?? ''}`.trim() : '', e.payout != null ? `выплата ${e.payout}` : '']
      .filter(Boolean)
      .join(' · '),
    partner: true,
  }));
  return [...funnel, ...fromPartner].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

@Component({
  selector: 'sim-client-card',
  imports: [
    ActivityFeedComponent, ReactiveFormsModule, RouterLink, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule, DatetimeFieldComponent,
    MatSelectModule, MatTooltipModule, AvatarComponent, ChannelIconComponent, EmptyStateComponent, StatusBadgeComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './client-card.component.html',
  styleUrl: './client-card.component.scss',
})
export class ClientCardComponent {
  readonly id = input.required<string>();

  private readonly api = inject(ClientsApi);
  private readonly route = inject(ActivatedRoute);
  private readonly injector = inject(Injector);
  private readonly conversation = viewChild<ElementRef<HTMLElement>>('conversation');
  private readonly toast = inject(ToastService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly stages = STAGES;
  protected readonly statuses = STATUSES.map((s) => ({ id: s, label: STATUS_LABELS[s] }));
  protected readonly partnerHint = PARTNER_ONLY_HINT;

  protected readonly form = new FormGroup({
    display_name: new FormControl('', { nonNullable: true }),
    next_step: new FormControl('', { nonNullable: true }),
    next_step_at: new FormControl<Date | null>(null),
    notes: new FormControl('', { nonNullable: true }),
  });
  protected readonly statusControl = new FormControl<ClientStatus>('active', { nonNullable: true });

  protected readonly card = signal<ClientCard | null>(null);
  protected readonly saving = signal(false);
  protected readonly loadError = signal('');
  private readonly reload = signal(0);

  protected readonly timeline = computed(() => {
    const c = this.card();
    return c ? buildTimeline(c.events, c.partner_events).map((t) => ({ ...t, when: formatDateTime(t.at) })) : [];
  });
  protected readonly createdAt = computed(() => formatDateTime(this.card()?.created_at));

  constructor() {
    combineLatest([toObservable(this.id), toObservable(this.reload)])
      .pipe(
        tap(() => { this.card.set(null); this.loadError.set(''); }),
        switchMap(([id]) => this.api.get(Number(id)).pipe(catchError((error: unknown) => {
          this.loadError.set(error instanceof HttpErrorResponse && error.status === 404 ? 'Клиент не найден' : 'Не удалось загрузить клиента');
          return EMPTY;
        }))),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((c) => this.applyCard(c));
  }

  protected retry(): void { this.reload.update((v) => v + 1); }

  protected save(): void {
    const c = this.card();
    if (!c) return;
    const v = this.form.getRawValue();
    this.saving.set(true);
    this.api
      .patch(c.id, {
        display_name: v.display_name.trim() || c.display_name,
        notes: v.notes,
        next_step: v.next_step,
        next_step_at: fromDate(v.next_step_at),
      })
      .pipe(
        catchError(() => {
          this.saving.set(false);
          return EMPTY;
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((updated) => {
        this.saving.set(false);
        this.card.set({ ...c, ...updated });
        this.form.markAsPristine();
        this.toast.info('Сохранено');
      });
  }

  protected setStage(stage: Stage): void {
    const c = this.card();
    if (!c || stage === c.stage) return;
    if (!stageMeta(stage).aiReachable) {
      this.toast.info(`Этап «${stageMeta(stage).label}» ${PARTNER_ONLY_HINT}`);
      return;
    }
    this.api
      .changeStage(c.id, { stage })
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((updated) => this.reloadAfterChange(updated.id));
  }

  protected setStatus(status: ClientStatus): void {
    const c = this.card();
    if (!c || status === c.status) return;
    this.api
      .changeStage(c.id, { status })
      .pipe(
        catchError(() => {
          this.statusControl.setValue(c.status, { emitEvent: false });
          return EMPTY;
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((updated) => this.reloadAfterChange(updated.id));
  }

  private reloadAfterChange(id: number): void {
    this.api
      .get(id)
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((c) => this.applyCard(c));
  }

  private applyCard(c: ClientCard): void {
    this.card.set(c);
    if (this.route.snapshot.fragment === 'conversation') {
      afterNextRender(() => this.conversation()?.nativeElement.scrollIntoView({ block: 'start' }), { injector: this.injector });
    }
    this.form.reset({
      display_name: c.display_name,
      next_step: c.next_step ?? '',
      next_step_at: toDate(c.next_step_at),
      notes: c.notes ?? '',
    });
    this.statusControl.setValue(c.status, { emitEvent: false });
  }
}
