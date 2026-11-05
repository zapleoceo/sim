import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { EMPTY, catchError, finalize } from 'rxjs';
import { AnalyticsApi } from '../../core/api/analytics.api';
import { PartnerImportResult } from '../../core/models';
import { PageHeaderComponent } from '../../shared/page-header.component';

const MAX_BYTES = 5 * 1024 * 1024;

@Component({
  selector: 'sim-partner',
  imports: [MatButtonModule, MatIconModule, PageHeaderComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page sim-page--narrow">
      <sim-page-header title="Партнёр" subtitle="Импорт регистраций и оплат из CSV-выгрузки партнёра" />

      <section class="sim-card">
        <h2>Формат файла</h2>
        <p class="sim-muted">Заголовок и колонки по порядку:</p>
        <code>client_ref,event,amount,currency,payout,occurred_at,external_id</code>
        <p class="sim-muted">
          <strong>event</strong> — registration или payment; <strong>client_ref</strong> — код из ссылки регистрации (?ref=…).
          Повторный импорт безопасен: записи с тем же external_id пропускаются.
        </p>
      </section>

      <section class="sim-card">
        <label class="drop" [class.over]="over()" (dragover)="onOver($event)" (dragleave)="over.set(false)" (drop)="onDrop($event)">
          <mat-icon class="material-icons-outlined">upload_file</mat-icon>
          <span>{{ file() ? file()!.name : 'Перетащите CSV сюда или выберите файл' }}</span>
          <input type="file" accept=".csv,text/csv" (change)="onPick($event)" />
        </label>
        @if (error()) {
          <p class="err" role="alert">{{ error() }}</p>
        }
        <div class="sim-row actions">
          <button mat-flat-button type="button" [disabled]="!file() || busy()" (click)="upload()">Импортировать</button>
        </div>
      </section>

      @if (result(); as r) {
        <section class="sim-card" aria-live="polite">
          <h2>Результат</h2>
          <div class="stats">
            <div><strong class="ok">{{ r.imported }}</strong><span class="sim-muted">импортировано</span></div>
            <div><strong>{{ r.skipped }}</strong><span class="sim-muted">пропущено</span></div>
            <div><strong [class.bad]="r.errors.length">{{ r.errors.length }}</strong><span class="sim-muted">ошибок</span></div>
          </div>
          @if (r.errors.length) {
            <ul class="errors">
              @for (e of r.errors; track $index) {
                <li>{{ e }}</li>
              }
            </ul>
          }
        </section>
      }
    </div>
  `,
  styles: `
    code { display: block; padding: var(--sim-space-2) var(--sim-space-3); margin: var(--sim-space-2) 0; border-radius: var(--sim-radius-sm); background: var(--sim-surface-2); overflow-x: auto; white-space: nowrap; }
    .drop {
      display: flex; flex-direction: column; align-items: center; gap: var(--sim-space-2); padding: var(--sim-space-6);
      border: 2px dashed var(--sim-border); border-radius: var(--sim-radius); cursor: pointer; color: var(--sim-text-muted); text-align: center;
    }
    .drop.over, .drop:focus-within { border-color: var(--sim-accent); background: var(--sim-accent-soft); }
    .drop mat-icon { font-size: 36px; width: 36px; height: 36px; }
    input[type='file'] { position: absolute; width: 1px; height: 1px; opacity: 0; }
    .actions { margin-top: var(--sim-space-3); }
    .err, .bad { color: var(--sim-danger); }
    .stats { display: flex; flex-wrap: wrap; gap: var(--sim-space-5); margin: var(--sim-space-3) 0; }
    .stats div { display: flex; flex-direction: column; }
    .stats strong { font-size: 26px; font-variant-numeric: tabular-nums; }
    .ok { color: var(--sim-ok); }
    .errors { margin: 0; padding-left: 20px; max-height: 280px; overflow-y: auto; font-size: 13px; color: var(--sim-danger); }
  `,
})
export class PartnerComponent {
  private readonly api = inject(AnalyticsApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly file = signal<File | null>(null);
  protected readonly over = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal('');
  protected readonly result = signal<PartnerImportResult | null>(null);

  protected onOver(ev: DragEvent): void {
    ev.preventDefault();
    this.over.set(true);
  }

  protected onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.over.set(false);
    this.setFile(ev.dataTransfer?.files[0] ?? null);
  }

  protected onPick(ev: Event): void {
    this.setFile((ev.target as HTMLInputElement).files?.[0] ?? null);
  }

  protected upload(): void {
    const f = this.file();
    if (!f) return;
    this.busy.set(true);
    this.result.set(null);
    this.api
      .importPartnerCsv(f)
      .pipe(
        finalize(() => this.busy.set(false)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((r) => {
        this.result.set(r);
        this.file.set(null);
      });
  }

  private setFile(f: File | null): void {
    this.error.set('');
    if (f && !/\.csv$/i.test(f.name)) {
      this.error.set('Нужен файл .csv');
      return;
    }
    if (f && f.size > MAX_BYTES) {
      this.error.set('Файл больше 5 МБ');
      return;
    }
    this.file.set(f);
  }
}
