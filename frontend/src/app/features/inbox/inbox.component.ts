import { BreakpointObserver } from '@angular/cdk/layout';
import {
  ChangeDetectionStrategy, Component, DestroyRef, ElementRef, computed, effect, inject, input, signal, viewChild,
} from '@angular/core';
import { takeUntilDestroyed, toObservable, toSignal } from '@angular/core/rxjs-interop';
import { TextFieldModule } from '@angular/cdk/text-field';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Router, RouterLink } from '@angular/router';
import {
  EMPTY, catchError, debounceTime, distinctUntilChanged, finalize, map, merge, startWith, switchMap, timer,
} from 'rxjs';
import { DialogsApi } from '../../core/api/dialogs.api';
import { PersonasApi } from '../../core/api/personas.api';
import { DialogDetail, DialogFilter, DialogRow, Message, Persona } from '../../core/models';
import { CHANNEL_KINDS, CHANNEL_LABELS, STAGES, STATUSES, STATUS_LABELS } from '../../core/stages';
import { AvatarComponent } from '../../shared/avatar.component';
import { ChannelIconComponent } from '../../shared/channel-icon.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { dayKey, dayLabel, formatShort, formatTime } from '../../shared/format';
import { StageChipComponent } from '../../shared/stage-chip.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';
import { activeFilterCount, createInboxFilterForm, normalizeFilter } from './inbox-filters';

const POLL_MS = 5000;

const MEDIA_LABELS: Record<string, { icon: string; label: string }> = {
  image: { icon: 'image', label: 'Фото' },
  photo: { icon: 'image', label: 'Фото' },
  video: { icon: 'videocam', label: 'Видео' },
  voice: { icon: 'mic', label: 'Голосовое' },
  audio: { icon: 'mic', label: 'Аудио' },
  document: { icon: 'description', label: 'Файл' },
};

interface RowVm {
  row: DialogRow;
  time: string;
  preview: string;
}

interface MessageVm {
  msg: Message;
  time: string;
  side: 'left' | 'right';
  authorLabel: string;
  media: { icon: string; label: string; isImage: boolean; isAudio: boolean } | null;
}

interface DayGroup { key: string; label: string; items: MessageVm[] }

@Component({
  selector: 'sim-inbox',
  imports: [
    ReactiveFormsModule, TextFieldModule, RouterLink, MatFormFieldModule, MatSelectModule, MatIconModule, MatButtonModule,
    MatSlideToggleModule, MatTooltipModule, AvatarComponent, ChannelIconComponent, EmptyStateComponent,
    StageChipComponent, StatusBadgeComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './inbox.component.html',
  styleUrl: './inbox.component.scss',
})
export class InboxComponent {
  /** Выбранный диалог — query-параметр `?d=`. */
  readonly d = input<string | undefined>();

  private readonly api = inject(DialogsApi);
  private readonly personasApi = inject(PersonasApi);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  protected readonly channelKinds = CHANNEL_KINDS;
  protected readonly channelLabels = CHANNEL_LABELS;
  protected readonly stages = STAGES;
  protected readonly statuses = STATUSES.map((id) => ({ id, label: STATUS_LABELS[id] }));

  protected readonly filters = createInboxFilterForm();
  protected readonly text = new FormControl('', { nonNullable: true });

  protected readonly canSend = toSignal(this.text.valueChanges.pipe(map((v) => v.trim().length > 0)), { initialValue: false });
  protected readonly isPhone = toSignal(
    inject(BreakpointObserver).observe('(max-width: 767px)').pipe(map((s) => s.matches)),
    { initialValue: false },
  );
  protected readonly isWide = toSignal(
    inject(BreakpointObserver).observe('(min-width: 1280px)').pipe(map((s) => s.matches)),
    { initialValue: false },
  );

  protected readonly personas = signal<Persona[]>([]);
  protected readonly rows = signal<DialogRow[]>([]);
  protected readonly total = signal(0);
  protected readonly listLoading = signal(true);
  protected readonly detail = signal<DialogDetail | null>(null);
  protected readonly sending = signal(false);
  protected readonly filtersOpen = signal(false);
  protected readonly cardOpen = signal(true);
  protected readonly filterCount = signal(0);

  protected readonly dialogId = computed(() => {
    const n = Number(this.d());
    return Number.isFinite(n) && n > 0 ? n : null;
  });
  protected readonly showList = computed(() => !this.isPhone() || this.dialogId() === null);
  protected readonly showConversation = computed(() => !this.isPhone() || this.dialogId() !== null);
  protected readonly showCard = computed(() => this.isWide() && this.cardOpen() && this.detail() !== null);

  protected readonly rowVms = computed<RowVm[]>(() =>
    this.rows().map((row) => ({
      row,
      time: formatShort(row.last_message?.at),
      preview: row.last_message ? (row.last_message.direction === 'out' ? 'Вы: ' : '') + row.last_message.text : 'Нет сообщений',
    })),
  );

  protected readonly groups = computed<DayGroup[]>(() => {
    const msgs = this.detail()?.messages ?? [];
    const out: DayGroup[] = [];
    for (const msg of msgs) {
      const key = dayKey(msg.created_at);
      let g = out[out.length - 1];
      if (g?.key !== key) {
        g = { key, label: dayLabel(msg.created_at), items: [] };
        out.push(g);
      }
      g.items.push(this.toVm(msg));
    }
    return out;
  });

  protected readonly messageCount = computed(() => this.detail()?.messages.length ?? 0);
  protected readonly selectedPersonaName = computed(() => {
    const id = this.detail()?.dialog.persona_id;
    return this.personas().find((p) => p.id === id)?.name ?? '';
  });

  constructor() {
    this.personasApi
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((p) => this.personas.set(p));

    // Список: при смене фильтров (q — с задержкой) и каждые 5 с, пока экран открыт.
    merge(
      this.filters.valueChanges.pipe(debounceTime(250)),
      timer(POLL_MS, POLL_MS),
    )
      .pipe(
        startWith(null),
        switchMap(() => {
          const filter: DialogFilter = normalizeFilter(this.filters.getRawValue());
          this.filterCount.set(activeFilterCount(filter));
          return this.api.list(filter, true).pipe(catchError(() => EMPTY));
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((res) => {
        this.rows.set(res.items);
        this.total.set(res.total ?? res.items.length);
        this.listLoading.set(false);
      });

    // Открытый диалог: загрузка + опрос каждые 5 с.
    toObservable(this.dialogId)
      .pipe(
        distinctUntilChanged(),
        switchMap((id) => {
          this.detail.set(null);
          if (id === null) return EMPTY;
          return timer(0, POLL_MS).pipe(
            switchMap((tick) => this.api.get(id, tick > 0).pipe(catchError(() => EMPTY))),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((d) => this.applyDetail(d));

    effect(() => {
      this.messageCount();
      this.dialogId();
      queueMicrotask(() => {
        const el = this.scroller()?.nativeElement;
        if (el) el.scrollTop = el.scrollHeight;
      });
    });
  }

  protected open(id: number): void {
    void this.router.navigate([], { queryParams: { d: id }, queryParamsHandling: 'merge' });
  }

  protected back(): void {
    void this.router.navigate([], { queryParams: { d: null }, queryParamsHandling: 'merge' });
  }

  protected resetFilters(): void {
    this.filters.reset();
  }

  protected toggleFilters(): void {
    this.filtersOpen.update((v) => !v);
  }

  protected toggleCard(): void {
    this.cardOpen.update((v) => !v);
  }

  /** Стрелки вверх/вниз перемещают фокус по списку диалогов. */
  protected onListKeydown(ev: KeyboardEvent): void {
    if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
    const items = Array.from((ev.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('button.row'));
    const i = items.indexOf(document.activeElement as HTMLElement);
    const next = items[ev.key === 'ArrowDown' ? i + 1 : i - 1];
    if (next) {
      ev.preventDefault();
      next.focus();
    }
  }

  protected onComposerKeydown(ev: KeyboardEvent): void {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      this.send();
    }
  }

  protected send(): void {
    const id = this.dialogId();
    const text = this.text.value.trim();
    if (id === null || !text || this.sending()) return;
    this.sending.set(true);
    this.api
      .send(id, text)
      .pipe(
        finalize(() => this.sending.set(false)),
        catchError(() => EMPTY),
      )
      .subscribe((msg) => {
        this.text.reset();
        this.detail.update((d) =>
          d ? { ...d, dialog: { ...d.dialog, ai_paused: true }, messages: [...d.messages, msg] } : d,
        );
        this.rows.update((rows) => rows.map((r) => (r.id === id ? { ...r, ai_paused: true } : r)));
      });
  }

  protected toggleAi(paused: boolean): void {
    const id = this.dialogId();
    if (id === null) return;
    this.api
      .setAiPaused(id, paused)
      .pipe(catchError(() => EMPTY))
      .subscribe((dialog) => {
        this.detail.update((d) => (d ? { ...d, dialog: { ...d.dialog, ai_paused: dialog.ai_paused } } : d));
        this.rows.update((rows) => rows.map((r) => (r.id === id ? { ...r, ai_paused: dialog.ai_paused } : r)));
      });
  }

  private applyDetail(d: DialogDetail): void {
    const prev = this.detail();
    // Не перерисовываем, если ничего не изменилось (сообщений столько же, пауза та же).
    const same =
      prev !== null &&
      prev.dialog.id === d.dialog.id &&
      prev.messages.length === d.messages.length &&
      prev.messages.at(-1)?.id === d.messages.at(-1)?.id &&
      prev.messages.at(-1)?.delivery === d.messages.at(-1)?.delivery &&
      prev.dialog.ai_paused === d.dialog.ai_paused;
    if (same) return;
    this.detail.set(d);
    const row = this.rows().find((r) => r.id === d.dialog.id);
    if (row && row.unread > 0) {
      this.rows.update((rows) => rows.map((r) => (r.id === d.dialog.id ? { ...r, unread: 0 } : r)));
      this.api.markRead(d.dialog.id).pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef)).subscribe();
    }
  }

  private toVm(msg: Message): MessageVm {
    const m = msg.media_kind ? (MEDIA_LABELS[msg.media_kind] ?? { icon: 'attach_file', label: 'Вложение' }) : null;
    return {
      msg,
      time: formatTime(msg.created_at),
      side: msg.author === 'client' ? 'left' : 'right',
      authorLabel: msg.author === 'ai' ? `ИИ${msg.model ? ' · ' + msg.model : ''}` : msg.author === 'operator' ? 'Оператор' : '',
      media: m ? { ...m, isImage: m.icon === 'image', isAudio: m.icon === 'mic' } : null,
    };
  }
}
