import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { MessageActivity } from '../core/activity.models';
import { Author, Delivery } from '../core/models';
import { CHANNEL_LABELS } from '../core/stages';
import { ChannelIconComponent } from './channel-icon.component';
import { formatDateTime } from './format';
import { MessageContentComponent } from './message-content.component';
import { MessageMetadataComponent } from './message-metadata.component';

export const DELIVERY_LABELS: Record<Delivery, string> = {
  queued: 'В очереди', sent: 'Отправлено', failed: 'Ошибка · нужна сверка', cancelled: 'Отменено',
};
const AUTHOR_LABELS: Record<Author, string> = { client: 'Клиент', ai: 'ИИ', operator: 'Оператор' };

@Component({
  selector: 'sim-message-card',
  imports: [RouterLink, MatButtonModule, ChannelIconComponent, MessageContentComponent, MessageMetadataComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <article class="sim-card" [attr.data-author]="message().author">
      <header>
        <strong>{{ message().direction === 'in' ? 'Входящее' : 'Исходящее' }} · {{ author() }}</strong>
        <time [attr.datetime]="message().created_at">{{ created() }}</time>
      </header>
      <div class="source">
        <sim-channel-icon [kind]="message().channel.kind" />
        <span>{{ channelLabel() }} · {{ message().channel.name }} @if (message().channel.account_label) { · {{ message().channel.account_label }} }</span>
      </div>
      <div class="context"><a [routerLink]="['/clients', message().client.id]">{{ message().client.display_name }}</a><span>Персонаж: {{ message().persona.name }}</span></div>
      <sim-message-content [message]="message()" />
      <sim-message-metadata [message]="message()" />
      <footer>
        <span class="sim-muted">№ {{ message().id }} @if (message().direction === 'out') { · {{ delivery() }} }</span>
        @if (message().sent_at) { <span class="sim-muted">Отправка подтверждена: {{ sent() }}</span> }
        <a mat-button routerLink="/inbox" [queryParams]="{ d: message().dialog_id }">Открыть диалог</a>
      </footer>
    </article>
  `,
  styles: `
    article { display: flex; flex-direction: column; gap: var(--sim-space-3); overflow-wrap: anywhere; }
    article[data-author='ai'] { border-left: var(--sim-space-1) solid var(--sim-accent); }
    article[data-author='operator'] { border-left: var(--sim-space-1) solid var(--sim-ok); }
    header, footer, .source, .context { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sim-space-2) var(--sim-space-3); }
    header { justify-content: space-between; } time, footer { font-size: 12px; }
    time { color: var(--sim-text-muted); } .source span { min-width: 0; flex: 1; } footer a { margin-left: auto; }
  `,
})
export class MessageCardComponent {
  readonly message = input.required<MessageActivity>();
  protected readonly author = computed(() => AUTHOR_LABELS[this.message().author]);
  protected readonly delivery = computed(() => DELIVERY_LABELS[this.message().delivery]);
  protected readonly channelLabel = computed(() => CHANNEL_LABELS[this.message().channel.kind]);
  protected readonly created = computed(() => formatDateTime(this.message().created_at));
  protected readonly sent = computed(() => formatDateTime(this.message().sent_at));
}
