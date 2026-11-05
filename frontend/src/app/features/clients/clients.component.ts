import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, debounceTime, startWith, switchMap, tap } from 'rxjs';
import { ClientsApi } from '../../core/api/clients.api';
import { PersonasApi } from '../../core/api/personas.api';
import { Client, ClientStatus, Persona, Stage } from '../../core/models';
import { STAGES, STATUSES, STATUS_LABELS } from '../../core/stages';
import { AvatarComponent } from '../../shared/avatar.component';
import { ChannelIconComponent } from '../../shared/channel-icon.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { formatShort } from '../../shared/format';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { StageChipComponent } from '../../shared/stage-chip.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

interface ClientVm { client: Client; last: string }

@Component({
  selector: 'sim-clients',
  imports: [
    ReactiveFormsModule, RouterLink, MatFormFieldModule, MatInputModule, MatSelectModule, MatIconModule,
    PageHeaderComponent, AvatarComponent, ChannelIconComponent, EmptyStateComponent, StageChipComponent, StatusBadgeComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page">
      <sim-page-header title="Клиенты" [subtitle]="total() + ' в выборке'" />

      <form class="sim-card filters" [formGroup]="filters">
        <mat-form-field appearance="outline" subscriptSizing="dynamic" class="q">
          <mat-icon matPrefix class="material-icons-outlined">search</mat-icon>
          <mat-label>Поиск по имени</mat-label>
          <input matInput type="search" formControlName="q" />
        </mat-form-field>
        <mat-form-field appearance="outline" subscriptSizing="dynamic">
          <mat-label>Этап</mat-label>
          <mat-select formControlName="stage">
            <mat-option value="">Все этапы</mat-option>
            @for (s of stages; track s.id) {
              <mat-option [value]="s.id">{{ s.label }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
        <mat-form-field appearance="outline" subscriptSizing="dynamic">
          <mat-label>Статус</mat-label>
          <mat-select formControlName="status">
            <mat-option value="">Любой</mat-option>
            @for (s of statuses; track s.id) {
              <mat-option [value]="s.id">{{ s.label }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
        <mat-form-field appearance="outline" subscriptSizing="dynamic">
          <mat-label>Персонаж</mat-label>
          <mat-select formControlName="persona_id">
            <mat-option [value]="null">Все</mat-option>
            @for (p of personas(); track p.id) {
              <mat-option [value]="p.id">{{ p.name }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
      </form>

      <ul class="sim-card list" role="list">
        @for (vm of vms(); track vm.client.id) {
          <li>
            <a class="item" [routerLink]="['/clients', vm.client.id]">
              <sim-avatar [name]="vm.client.display_name" [url]="vm.client.avatar_url" [size]="40" />
              <span class="name">
                <strong>{{ vm.client.display_name }}</strong>
                <span class="sim-muted">{{ vm.client.next_step || 'Следующий шаг не задан' }}</span>
              </span>
              <span class="tags">
                <sim-stage-chip [stage]="vm.client.stage" />
                <sim-status-badge [status]="vm.client.status" />
              </span>
              <span class="channels">
                @for (c of vm.client.channels; track c.dialog_id) {
                  <sim-channel-icon [kind]="c.kind" />
                }
              </span>
              <span class="sim-muted last">{{ vm.last }}</span>
            </a>
          </li>
        } @empty {
          @if (!loading()) {
            <sim-empty-state icon="groups" title="Клиентов не найдено" hint="Измените фильтры или дождитесь первых диалогов." />
          }
        }
      </ul>
    </div>
  `,
  styles: `
    .filters { display: grid; grid-template-columns: 2fr repeat(3, 1fr); gap: var(--sim-space-3); padding: var(--sim-space-3) var(--sim-space-4); }
    .list { list-style: none; margin: 0; padding: 0; overflow: hidden; }
    .item {
      display: grid; grid-template-columns: 40px minmax(0, 1fr) auto auto 64px; align-items: center; gap: var(--sim-space-3);
      padding: var(--sim-space-3) var(--sim-space-4); color: inherit; text-decoration: none; border-bottom: 1px solid var(--sim-border);
    }
    li:last-child .item { border-bottom: 0; }
    .item:hover { background: var(--sim-surface-2); }
    .item:focus-visible { outline-offset: -2px; }
    .name { display: flex; flex-direction: column; min-width: 0; span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } }
    .tags, .channels { display: flex; align-items: center; gap: var(--sim-space-2); }
    .last { text-align: right; font-size: 12px; }
    @media (max-width: 767px) {
      .filters { grid-template-columns: 1fr 1fr; }
      .q { grid-column: 1 / -1; }
      .item { grid-template-columns: 40px minmax(0, 1fr) auto; }
      .tags { grid-column: 2 / 4; grid-row: 2; flex-wrap: wrap; }
      .channels { display: none; }
    }
  `,
})
export class ClientsComponent {
  private readonly api = inject(ClientsApi);
  private readonly personasApi = inject(PersonasApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly stages = STAGES;
  protected readonly statuses = STATUSES.map((id) => ({ id, label: STATUS_LABELS[id] }));

  protected readonly filters = new FormGroup({
    q: new FormControl('', { nonNullable: true }),
    stage: new FormControl<Stage | ''>('', { nonNullable: true }),
    status: new FormControl<ClientStatus | ''>('', { nonNullable: true }),
    persona_id: new FormControl<number | null>(null),
  });

  protected readonly personas = signal<Persona[]>([]);
  protected readonly clients = signal<Client[]>([]);
  protected readonly total = signal(0);
  protected readonly loading = signal(true);

  protected readonly vms = computed<ClientVm[]>(() =>
    this.clients().map((client) => ({ client, last: formatShort(client.last_contact_at) })),
  );

  constructor() {
    this.personasApi
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((p) => this.personas.set(p));

    this.filters.valueChanges
      .pipe(
        startWith(null),
        debounceTime(250),
        tap(() => this.loading.set(true)),
        switchMap(() =>
          this.api.list({ ...this.filters.getRawValue(), q: this.filters.controls.q.value.trim() }).pipe(
            catchError(() => {
              this.loading.set(false);
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((res) => {
        this.clients.set(res.items);
        this.total.set(res.total ?? res.items.length);
        this.loading.set(false);
      });
  }
}
