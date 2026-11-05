import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { EMPTY, catchError, forkJoin, of, switchMap } from 'rxjs';
import { ChannelsApi } from '../../core/api/channels.api';
import { PersonasApi } from '../../core/api/personas.api';
import { Channel, ChannelStatus, Persona } from '../../core/models';
import { ConfirmService } from '../../shared/confirm-dialog.component';
import { ChannelIconComponent } from '../../shared/channel-icon.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { formatDateTime } from '../../shared/format';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { ChannelWizardComponent, WizardData } from './channel-wizard.component';

const STATUS_VIEW: Record<ChannelStatus, { label: string; tone: 'ok' | 'warn' | 'bad' | 'idle' }> = {
  new: { label: 'Не подключён', tone: 'idle' },
  pending: { label: 'Ожидает подключения', tone: 'warn' },
  active: { label: 'Работает', tone: 'ok' },
  challenge: { label: 'Нужно подтверждение', tone: 'warn' },
  expired: { label: 'Сессия истекла', tone: 'bad' },
  error: { label: 'Ошибка', tone: 'bad' },
};

@Component({
  selector: 'sim-channels',
  imports: [MatButtonModule, MatIconModule, PageHeaderComponent, ChannelIconComponent, EmptyStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page">
      <sim-page-header title="Каналы" subtitle="Telegram, WhatsApp и Instagram, через которые персонажи общаются с клиентами">
        <button mat-flat-button type="button" (click)="openWizard()" [disabled]="!personas().length">
          <mat-icon class="material-icons-outlined">add</mat-icon>
          Добавить канал
        </button>
      </sim-page-header>

      @if (loaded() && !personas().length) {
        <p class="sim-card note">Сначала создайте хотя бы одного персонажа — канал привязывается к нему.</p>
      }

      <div class="grid">
        @for (vm of vms(); track vm.channel.id) {
          <article class="sim-card ch">
            <header>
              <sim-channel-icon [kind]="vm.channel.kind" />
              <h3 class="sim-grow">{{ vm.channel.name }}</h3>
              <span class="status" [attr.data-tone]="vm.tone">{{ vm.label }}</span>
            </header>
            <dl>
              <div><dt class="sim-muted">Персонаж</dt><dd>{{ vm.persona }}</dd></div>
              @if (vm.channel.account_label) {
                <div><dt class="sim-muted">Аккаунт</dt><dd>{{ vm.channel.account_label }}</dd></div>
              }
              @if (vm.activity) {
                <div><dt class="sim-muted">Активность</dt><dd>{{ vm.activity }}</dd></div>
              }
            </dl>
            @if (vm.channel.last_error) {
              <p class="err" role="alert">{{ vm.channel.last_error }}</p>
            }
            <footer>
              @if (vm.channel.status !== 'active') {
                <button mat-stroked-button type="button" (click)="openWizard(vm.channel)">Подключить</button>
              }
              <span class="sim-grow"></span>
              <button mat-button type="button" class="danger" (click)="disconnect(vm.channel)">Отключить</button>
            </footer>
          </article>
        } @empty {
          @if (loaded()) {
            <sim-empty-state icon="cable" title="Каналов пока нет" hint="Добавьте Telegram-бота, номер WhatsApp или аккаунт Instagram." />
          }
        }
      </div>
    </div>
  `,
  styles: `
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: var(--sim-space-4); }
    .ch { display: flex; flex-direction: column; gap: var(--sim-space-3); }
    header, footer { display: flex; align-items: center; gap: var(--sim-space-2); }
    h3 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    dl { margin: 0; display: flex; flex-direction: column; gap: 4px; }
    dl div { display: flex; justify-content: space-between; gap: var(--sim-space-3); }
    dt, dd { margin: 0; }
    .status { padding: 0 8px; border-radius: 6px; font-size: 12px; line-height: 22px; white-space: nowrap; background: var(--sim-surface-2); color: var(--sim-text-muted); }
    .status[data-tone='ok'] { color: var(--sim-ok); background: color-mix(in srgb, var(--sim-ok) 14%, transparent); }
    .status[data-tone='warn'] { color: var(--sim-warn); background: color-mix(in srgb, var(--sim-warn) 14%, transparent); }
    .status[data-tone='bad'] { color: var(--sim-danger); background: color-mix(in srgb, var(--sim-danger) 14%, transparent); }
    .err { color: var(--sim-danger); font-size: 13px; overflow-wrap: anywhere; }
    .danger { color: var(--sim-danger); }
    .note { color: var(--sim-text-muted); }
  `,
})
export class ChannelsComponent {
  private readonly api = inject(ChannelsApi);
  private readonly personasApi = inject(PersonasApi);
  private readonly dialog = inject(MatDialog);
  private readonly confirm = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly channels = signal<Channel[]>([]);
  protected readonly personas = signal<Persona[]>([]);
  protected readonly loaded = signal(false);

  protected readonly vms = computed(() =>
    this.channels().map((channel) => ({
      channel,
      ...STATUS_VIEW[channel.status],
      persona: this.personas().find((p) => p.id === channel.persona_id)?.name ?? '—',
      activity: formatDateTime(channel.last_activity_at),
    })),
  );

  constructor() {
    forkJoin({ channels: this.api.list(), personas: this.personasApi.list() })
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((r) => {
        this.channels.set(r.channels);
        this.personas.set(r.personas);
        this.loaded.set(true);
      });
  }

  protected openWizard(channel?: Channel): void {
    this.dialog
      .open<ChannelWizardComponent, WizardData, boolean>(ChannelWizardComponent, {
        data: { personas: this.personas(), channel },
        width: '520px',
        maxWidth: '96vw',
        disableClose: true,
      })
      .afterClosed()
      .pipe(
        switchMap((changed) => (changed ? this.api.list().pipe(catchError(() => EMPTY)) : of(null))),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((list) => {
        if (list) this.channels.set(list);
      });
  }

  protected disconnect(channel: Channel): void {
    this.confirm
      .confirm({
        title: 'Отключить канал?',
        message: `«${channel.name}» перестанет принимать и отправлять сообщения, сессия будет отвязана.`,
        confirmLabel: 'Отключить',
        danger: true,
      })
      .pipe(
        switchMap(() => this.api.remove(channel.id)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => this.channels.update((list) => list.filter((c) => c.id !== channel.id)));
  }
}
