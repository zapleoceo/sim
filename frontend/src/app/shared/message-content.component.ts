import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { Message } from '../core/models';
import { GuardBadgeComponent } from './guard-badge.component';

const MEDIA_LABELS: Record<string, { icon: string; label: string }> = {
  image: { icon: 'image', label: 'Фото' }, photo: { icon: 'image', label: 'Фото' },
  video: { icon: 'videocam', label: 'Видео' }, voice: { icon: 'mic', label: 'Голосовое' },
  audio: { icon: 'mic', label: 'Аудио' }, document: { icon: 'description', label: 'Файл' },
};

@Component({
  selector: 'sim-message-content',
  imports: [MatIconModule, GuardBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (media(); as media) {
      @if (media.icon === 'image' && message().media_url) {
        <a [href]="message().media_url" target="_blank" rel="noopener"><img [src]="message().media_url" [alt]="media.label" loading="lazy" /></a>
      } @else if (media.icon === 'mic' && message().media_url) {
        <audio controls preload="none" [src]="message().media_url"></audio>
      } @else {
        <span class="media"><mat-icon class="material-icons-outlined">{{ media.icon }}</mat-icon>
          @if (message().media_url) { <a [href]="message().media_url" target="_blank" rel="noopener">{{ media.label }}</a> }
          @else { {{ media.label }} }
        </span>
      }
    }
    @if (message().text) { <p>{{ message().text }}</p> }
    @if (message().author === 'ai' && message().guard; as guard) { <sim-guard-badge [guard]="guard" /> }
    @if (message().delivery === 'failed') { <p class="warning">Ошибка отправки. Результат требует сверки с каналом.</p> }
  `,
  styles: `
    :host { display: flex; min-width: 0; flex-direction: column; gap: var(--sim-space-2); }
    p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
    img { display: block; max-width: 100%; max-height: 280px; border-radius: var(--sim-radius-sm); object-fit: contain; }
    audio { width: 100%; max-width: 320px; }
    .media { display: flex; align-items: center; gap: var(--sim-space-2); }
    .warning { color: var(--sim-danger); font-size: 12px; }
  `,
})
export class MessageContentComponent {
  readonly message = input.required<Message>();
  protected readonly media = computed(() => {
    const kind = this.message().media_kind;
    return kind ? (MEDIA_LABELS[kind] ?? { icon: 'attach_file', label: 'Вложение' }) : null;
  });
}
