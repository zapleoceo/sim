import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import { StudioApi, StudioMessage, StudioState } from '../../core/api/studio.api';
import { errorMessage } from '../../core/interceptors';

@Component({
  selector: 'sim-studio',
  imports: [FormsModule],
  templateUrl: './studio.component.html',
  styleUrl: './studio.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StudioComponent {
  private readonly api = inject(StudioApi);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly state = signal<StudioState | null>(null);
  protected readonly loading = signal(true);
  protected readonly sending = signal(false);
  protected readonly error = signal('');
  protected readonly messages = signal<StudioMessage[]>([]);
  protected model = '';
  protected draft = '';

  constructor() { this.load(); }

  protected load(): void {
    this.loading.set(true);
    this.error.set('');
    this.api.get().pipe(takeUntilDestroyed(this.destroyRef), finalize(() => this.loading.set(false))).subscribe({
      next: (state) => { this.state.set(state); this.model = state.models[0] ?? ''; },
      error: (err: unknown) => this.error.set(errorMessage(err)),
    });
  }

  protected clearConversation(): void {
    if (this.sending()) return;
    this.messages.set([]);
    this.error.set('');
  }

  protected send(): void {
    const content = this.draft.trim();
    const state = this.state();
    if (!content || !state?.text_available || !state.models.includes(this.model) || this.sending()) return;
    const pending: StudioMessage[] = [...this.messages(), { role: 'user', content }];
    if (pending.length > 24 || pending.reduce((total, message) => total + [...message.content].length, 0) > 32000) {
      this.error.set('Достигнут лимит разговора: 24 сообщения или 32 000 символов. Сохраните нужную переписку и нажмите «Новый разговор». Черновик сохранится.');
      return;
    }
    this.sending.set(true);
    this.error.set('');
    this.api.chat(this.model, pending).pipe(takeUntilDestroyed(this.destroyRef), finalize(() => this.sending.set(false))).subscribe({
      next: (result) => {
        this.messages.set([...pending, { role: 'assistant', content: result.text }]);
        this.draft = '';
      },
      error: (err: unknown) => this.error.set(errorMessage(err)),
    });
  }
}
