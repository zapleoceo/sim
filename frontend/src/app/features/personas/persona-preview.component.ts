import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { JsonPipe } from '@angular/common';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { EMPTY, catchError, finalize } from 'rxjs';
import { PersonasApi } from '../../core/api/personas.api';
import { PreviewTurn } from '../../core/models';

interface PreviewMsg { role: 'user' | 'assistant'; text: string; meta?: string; decision?: unknown }

@Component({
  selector: 'sim-persona-preview',
  imports: [ReactiveFormsModule, JsonPipe, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="head">
      <h3>Проверить персонажа</h3>
      <button mat-button type="button" (click)="reset()" [disabled]="!messages().length">Очистить</button>
    </div>
    <p class="sim-muted hint">Пробный ответ без отправки клиенту. Проверяются сохранённые настройки.</p>

    <div class="chat" role="log" aria-live="polite">
      @for (m of messages(); track $index) {
        <div class="msg" [class.me]="m.role === 'user'">
          <div class="bubble">{{ m.text }}</div>
          @if (m.meta) {
            <span class="meta sim-muted">{{ m.meta }}</span>
          }
          @if (m.decision) {
            <details>
              <summary class="sim-muted">Решение модели</summary>
              <pre>{{ m.decision | json }}</pre>
            </details>
          }
        </div>
      } @empty {
        <p class="sim-muted center">Напишите сообщение, как это сделал бы клиент.</p>
      }
      @if (busy()) {
        <div class="msg"><div class="bubble typing">печатает…</div></div>
      }
    </div>

    <form class="row" (ngSubmit)="send()">
      <mat-form-field appearance="outline" subscriptSizing="dynamic" class="sim-grow">
        <mat-label>Сообщение</mat-label>
        <input matInput [formControl]="text" autocomplete="off" />
      </mat-form-field>
      <button mat-flat-button type="submit" [disabled]="busy()" aria-label="Отправить">
        <mat-icon class="material-icons-outlined">send</mat-icon>
      </button>
    </form>
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: var(--sim-space-2); min-height: 0; }
    .head { display: flex; align-items: center; justify-content: space-between; }
    .hint { font-size: 12px; }
    .chat { display: flex; flex-direction: column; gap: var(--sim-space-2); min-height: 200px; max-height: 420px; overflow-y: auto; padding: var(--sim-space-3); background: var(--sim-surface-2); border-radius: var(--sim-radius-sm); }
    .msg { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; &.me { align-items: flex-end; } }
    .bubble { max-width: 90%; padding: 8px 12px; border-radius: 14px; background: var(--sim-bubble-ai); white-space: pre-wrap; overflow-wrap: anywhere; }
    .me .bubble { background: var(--sim-bubble-client); }
    .typing { opacity: 0.6; }
    .meta { font-size: 11px; }
    details { font-size: 12px; max-width: 100%; }
    pre { margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; }
    .center { text-align: center; margin: auto; }
    .row { display: flex; gap: var(--sim-space-2); align-items: flex-start; }
    .row button { height: 56px; }
  `,
})
export class PersonaPreviewComponent {
  readonly personaId = input.required<number>();

  private readonly api = inject(PersonasApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly text = new FormControl('', { nonNullable: true });
  protected readonly messages = signal<PreviewMsg[]>([]);
  protected readonly busy = signal(false);

  constructor() {
    // Другой персонаж — чистый чат.
    effect(() => {
      this.personaId();
      this.messages.set([]);
    });
  }

  protected reset(): void {
    this.messages.set([]);
  }

  protected send(): void {
    const message = this.text.value.trim();
    if (!message || this.busy()) return;
    const history: PreviewTurn[] = this.messages().map((m) => ({ role: m.role, text: m.text }));
    this.messages.update((m) => [...m, { role: 'user', text: message }]);
    this.text.reset();
    this.busy.set(true);
    this.api
      .preview(this.personaId(), message, history)
      .pipe(
        finalize(() => this.busy.set(false)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((r) =>
        this.messages.update((m) => [
          ...m,
          { role: 'assistant', text: r.reply, meta: `${r.model} · ${r.latency_ms} мс`, decision: r.decision },
        ]),
      );
  }
}
