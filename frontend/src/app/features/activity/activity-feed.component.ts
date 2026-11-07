import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatPaginatorIntl, MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { RouterLink } from '@angular/router';
import { catchError, map, of, startWith, switchMap } from 'rxjs';
import { ActivityApi } from '../../core/api/activity.api';
import { LlmActivity, MessageActivity } from '../../core/activity.models';
import { AuthService } from '../../core/auth.service';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { dayKey, dayLabel, formatDateTime, formatMoney } from '../../shared/format';
import { MessageCardComponent } from '../../shared/message-card.component';
import { RuPaginatorIntl } from '../../shared/paginator-intl';
import { metric } from '../../shared/message-metadata.component';
import { ActivityFilters, ActivityFiltersComponent } from './activity-filters.component';

interface FeedState { loading: boolean; error: boolean; total: number; messages: MessageActivity[]; calls: LlmActivity[] }
const EMPTY_STATE: FeedState = { loading: false, error: false, total: 0, messages: [], calls: [] };

@Component({
  selector: 'sim-activity-feed',
  providers: [{ provide: MatPaginatorIntl, useClass: RuPaginatorIntl }],
  imports: [ActivityFiltersComponent, MessageCardComponent, EmptyStateComponent, MatButtonModule, MatPaginatorModule, RouterLink],
  templateUrl: './activity-feed.component.html',
  styleUrl: './activity-feed.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ActivityFeedComponent {
  readonly clientId = input<number>();
  readonly channelId = input<number | null>(null);
  readonly llm = input(false);
  private readonly api = inject(ActivityApi);
  private readonly auth = inject(AuthService);
  protected readonly filters = signal<ActivityFilters | null>(null);
  protected readonly page = signal({ limit: 25, offset: 0 });
  private readonly revision = signal(0);
  protected readonly state = signal<FeedState>(EMPTY_STATE);
  protected readonly pageIndex = computed(() => this.page().offset / this.page().limit);
  protected readonly allowed = computed(() => !this.llm() || this.auth.isOwner());
  protected readonly timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  private readonly request = computed(() => ({
    filters: this.filters(), page: this.page(), client: this.clientId(), llm: this.llm(), allowed: this.allowed(), revision: this.revision(),
  }));
  protected readonly messages = computed(() => {
    const items = this.clientId() === undefined ? this.state().messages : [...this.state().messages].reverse();
    return items.map((message, index) => ({ message,
      day: index === 0 || dayKey(items[index - 1].created_at) !== dayKey(message.created_at) ? dayLabel(message.created_at) : '',
    }));
  });
  protected readonly calls = computed(() => this.state().calls.map((call) => ({
    call, at: formatDateTime(call.created_at), tokensIn: metric(call.tokens_in), tokensOut: metric(call.tokens_out),
    cached: metric(call.cached_tokens), cost: call.cost_usd == null ? '—' : formatMoney(call.cost_usd, 'USD', 6),
  })));

  constructor() {
    toObservable(this.request).pipe(switchMap((r) => {
      if (!r.allowed || r.filters === null) return of(EMPTY_STATE);
      const request = r.llm
        ? this.api.llm(r.filters, r.page).pipe(map((p): FeedState => ({ ...EMPTY_STATE, calls: p.items, total: p.total })))
        : this.api.messages(r.filters, r.page, r.client).pipe(map((p): FeedState => ({ ...EMPTY_STATE, messages: p.items, total: p.total })));
      return request.pipe(catchError(() => of({ ...EMPTY_STATE, error: true })), startWith({ ...EMPTY_STATE, loading: true }));
    }), takeUntilDestroyed()).subscribe((state) => this.state.set(state));
  }

  protected setFilters(filters: ActivityFilters | null): void {
    this.page.update((page) => ({ ...page, offset: 0 }));
    this.filters.set(filters);
  }
  protected paginate(event: PageEvent): void { this.page.set({ limit: event.pageSize, offset: event.pageIndex * event.pageSize }); }
  protected refresh(): void {
    this.page.update((page) => ({ ...page, offset: 0 }));
    this.revision.update((v) => v + 1);
  }
  protected retry(): void { this.revision.update((v) => v + 1); }
}
