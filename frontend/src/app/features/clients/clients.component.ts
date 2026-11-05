import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatPaginatorIntl, MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { MatSelectModule } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, debounceTime, map, startWith, switchMap, tap } from 'rxjs';
import { ClientsApi } from '../../core/api/clients.api';
import { PersonasApi } from '../../core/api/personas.api';
import { Client, ClientStatus, Persona, Stage } from '../../core/models';
import { STAGES, STATUSES, STATUS_LABELS } from '../../core/stages';
import { AvatarComponent } from '../../shared/avatar.component';
import { ChannelIconComponent } from '../../shared/channel-icon.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { FilterBarComponent, FiltersTemplateDirective } from '../../shared/filter-bar.component';
import { formatShort } from '../../shared/format';
import { RuPaginatorIntl } from '../../shared/paginator-intl';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { StageChipComponent } from '../../shared/stage-chip.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

interface ClientVm { client: Client; last: string }

@Component({
  selector: 'sim-clients',
  imports: [
    ReactiveFormsModule, RouterLink, MatFormFieldModule, MatInputModule, MatSelectModule, MatIconModule,
    MatPaginatorModule, FilterBarComponent, FiltersTemplateDirective, PageHeaderComponent, AvatarComponent,
    ChannelIconComponent, EmptyStateComponent, StageChipComponent, StatusBadgeComponent,
  ],
  providers: [{ provide: MatPaginatorIntl, useClass: RuPaginatorIntl }],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page">
      <sim-page-header title="Клиенты" [subtitle]="total() + ' в выборке'" />

      <form [formGroup]="filters">
        <sim-filter-bar [activeCount]="activeCount()" (cleared)="resetFilters()">
          <mat-form-field simSearch appearance="outline" subscriptSizing="dynamic" class="sim-search-field">
            <mat-icon matPrefix class="material-icons-outlined">search</mat-icon>
            <mat-label>Поиск по имени</mat-label>
            <input matInput type="search" formControlName="q" />
          </mat-form-field>
          <ng-template simFilters>
            <mat-form-field appearance="outline" subscriptSizing="dynamic" class="sim-filter-field">
              <mat-label>Этап</mat-label>
              <mat-select formControlName="stage">
                <mat-option value="">Все этапы</mat-option>
                @for (s of stages; track s.id) {
                  <mat-option [value]="s.id">{{ s.label }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" subscriptSizing="dynamic" class="sim-filter-field">
              <mat-label>Статус</mat-label>
              <mat-select formControlName="status">
                <mat-option value="">Любой</mat-option>
                @for (s of statuses; track s.id) {
                  <mat-option [value]="s.id">{{ s.label }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field floatLabel="always" appearance="outline" subscriptSizing="dynamic" class="sim-filter-field">
              <mat-label>Персонаж</mat-label>
              <mat-select placeholder="Все персонажи" formControlName="persona_id">
                <mat-option [value]="null">Все</mat-option>
                @for (p of personas(); track p.id) {
                  <mat-option [value]="p.id">{{ p.name }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
          </ng-template>
        </sim-filter-bar>
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

      @if (total() > pageSizes[0]) {
        <mat-paginator
          class="sim-card pager"
          [length]="total()"
          [pageSize]="pageSize()"
          [pageIndex]="pageIndex()"
          [pageSizeOptions]="pageSizes"
          (page)="onPage($event)"
          aria-label="Страницы списка клиентов"
        />
      }
    </div>
  `,
  styles: `
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
    .pager { padding: 0; --mat-paginator-container-background-color: transparent; }
    @media (max-width: 767px) {
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
  protected readonly pageSizes = [25, 50, 100];

  protected readonly filters = new FormGroup({
    q: new FormControl('', { nonNullable: true }),
    stage: new FormControl<Stage | ''>('', { nonNullable: true }),
    status: new FormControl<ClientStatus | ''>('', { nonNullable: true }),
    persona_id: new FormControl<number | null>(null),
  });

  protected readonly pageIndex = signal(0);
  protected readonly pageSize = signal(50);
  protected readonly personas = signal<Persona[]>([]);
  protected readonly clients = signal<Client[]>([]);
  protected readonly total = signal(0);
  protected readonly loading = signal(true);
  protected readonly activeCount = signal(0);

  protected readonly vms = computed<ClientVm[]>(() =>
    this.clients().map((client) => ({ client, last: formatShort(client.last_contact_at) })),
  );

  constructor() {
    this.personasApi
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((p) => this.personas.set(p));

    // Смена фильтра сразу (до debounce) возвращает на первую страницу.
    this.filters.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => this.pageIndex.set(0));

    combineLatest([
      this.filters.valueChanges.pipe(startWith(null), debounceTime(250)),
      toObservable(this.pageIndex),
      toObservable(this.pageSize),
    ])
      .pipe(
        map(([, index, size]) => ({ index, size })),
        tap(() => {
          this.loading.set(true);
          this.activeCount.set(this.countActive());
        }),
        switchMap(({ index, size }) =>
          this.api
            .list(
              { ...this.filters.getRawValue(), q: this.filters.controls.q.value.trim() },
              { limit: size, offset: index * size },
            )
            .pipe(
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

  protected onPage(ev: PageEvent): void {
    this.pageSize.set(ev.pageSize);
    this.pageIndex.set(ev.pageIndex);
  }

  protected resetFilters(): void {
    this.filters.reset({ q: '', stage: '', status: '', persona_id: null });
  }

  private countActive(): number {
    const v = this.filters.getRawValue();
    return [v.q.trim() !== '', v.stage !== '', v.status !== '', v.persona_id !== null].filter(Boolean).length;
  }
}
