import { TextFieldModule } from '@angular/cdk/text-field';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { EMPTY, catchError } from 'rxjs';
import { SettingsApi } from '../../core/api/settings.api';
import { AppSettingItem } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { SettingGroup, groupSettings, parseSettingValue } from './settings-groups';

/** Все общие промпты и параметры на одной странице: каждое поле открыто, без скрытых секций. */
@Component({
  selector: 'sim-settings',
  imports: [TextFieldModule, MatButtonModule, MatFormFieldModule, MatInputModule, PageHeaderComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page">
      <sim-page-header
        title="Промпты и настройки"
        subtitle="Общие для всех персонажей. Хранятся в базе: деплой их не сбрасывает."
      />
      @for (group of groups(); track group.name) {
        <section class="sim-card group">
          <h2>{{ group.name }}</h2>
          @for (s of group.items; track s.key) {
            <div class="setting">
              <mat-form-field appearance="outline" class="block">
                <mat-label>{{ s.label }}</mat-label>
                @if (s.kind === 'text') {
                  <textarea
                    matInput
                    cdkTextareaAutosize
                    cdkAutosizeMinRows="3"
                    cdkAutosizeMaxRows="30"
                    [value]="s.value"
                    (input)="edit(s.key, $event)"
                  ></textarea>
                } @else {
                  <input
                    matInput
                    [type]="s.kind === 'int' ? 'number' : 'text'"
                    [value]="s.value"
                    (input)="edit(s.key, $event)"
                  />
                }
                <mat-hint>{{ s.help }}@if (s.min !== null) { ({{ s.min }}–{{ s.max }})}</mat-hint>
              </mat-form-field>
              <div class="actions">
                <span class="sim-muted state">{{ s.is_default ? 'по умолчанию' : 'изменено' }}</span>
                <span class="sim-grow"></span>
                @if (!s.is_default) {
                  <button mat-button type="button" [disabled]="busy() === s.key" (click)="reset(s)">
                    Вернуть по умолчанию
                  </button>
                }
                <button mat-flat-button type="button" [disabled]="busy() === s.key" (click)="save(s)">
                  Сохранить
                </button>
              </div>
            </div>
          }
        </section>
      }
    </div>
  `,
  styles: `
    .group { display: flex; flex-direction: column; gap: var(--sim-space-3); margin-bottom: var(--sim-space-3); }
    .group h2 { margin: 0; font-size: 16px; font-weight: 600; }
    .block { width: 100%; }
    .setting { display: flex; flex-direction: column; gap: var(--sim-space-1); }
    .actions { display: flex; align-items: center; gap: var(--sim-space-2); flex-wrap: wrap; }
    .state { font-size: 12px; }
  `,
})
export class SettingsComponent {
  private readonly api = inject(SettingsApi);
  private readonly toast = inject(ToastService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly groups = signal<SettingGroup[]>([]);
  protected readonly busy = signal<string | null>(null);
  private items: AppSettingItem[] = [];
  private readonly drafts = new Map<string, string>();

  constructor() {
    this.api
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((items) => this.show(items));
  }

  protected edit(key: string, event: Event): void {
    this.drafts.set(key, (event.target as HTMLInputElement | HTMLTextAreaElement).value);
  }

  protected save(item: AppSettingItem): void {
    const value = parseSettingValue(item, this.drafts.get(item.key) ?? String(item.value));
    if (value === null) {
      this.toast.error(`«${item.label}»: нужно целое число`);
      return;
    }
    this.apply(item, this.api.save(item.key, value), 'Сохранено');
  }

  protected reset(item: AppSettingItem): void {
    this.apply(item, this.api.reset(item.key), 'Возвращено значение по умолчанию');
  }

  private apply(item: AppSettingItem, call: ReturnType<SettingsApi['save']>, done: string): void {
    this.busy.set(item.key);
    call.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (updated) => {
        this.drafts.delete(updated.key);
        this.show(this.items.map((i) => (i.key === updated.key ? updated : i)));
        this.busy.set(null);
        this.toast.info(done);
      },
      error: () => this.busy.set(null), // the HTTP interceptor already shows the error
    });
  }

  private show(items: AppSettingItem[]): void {
    this.items = items;
    this.groups.set(groupSettings(items));
  }
}
