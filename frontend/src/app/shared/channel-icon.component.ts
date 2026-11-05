import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ChannelKind } from '../core/models';
import { CHANNEL_LABELS } from '../core/stages';

const ICONS: Record<ChannelKind, string> = {
  telegram: 'send',
  telegram_user: 'person',
  whatsapp: 'chat',
  instagram: 'photo_camera',
};

@Component({
  selector: 'sim-channel-icon',
  imports: [MatIconModule, MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[attr.data-kind]': 'kind()', role: 'img', '[attr.aria-label]': 'label()' },
  template: `<mat-icon class="material-icons-outlined" [matTooltip]="label()">{{ icon() }}</mat-icon>`,
  styles: `
    :host { display: inline-flex; width: 24px; height: 24px; border-radius: 6px; align-items: center; justify-content: center; color: #fff; }
    :host([data-kind='telegram']) { background: #2a8fd1; }
    :host([data-kind='telegram_user']) { background: #1c6fa8; }
    :host([data-kind='whatsapp']) { background: #25a35a; }
    :host([data-kind='instagram']) { background: linear-gradient(135deg, #a255c9, #e0527a 60%, #f0963c); }
    mat-icon { font-size: 15px; width: 15px; height: 15px; }
  `,
})
export class ChannelIconComponent {
  readonly kind = input.required<ChannelKind>();

  protected readonly icon = computed(() => ICONS[this.kind()]);
  protected readonly label = computed(() => CHANNEL_LABELS[this.kind()]);
}
