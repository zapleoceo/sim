import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, finalize, switchMap } from 'rxjs';
import { StrategiesApi } from '../../core/api/strategies.api';
import { Strategy } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { ConfirmService } from '../../shared/confirm-dialog.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { PLAYBOOK_STAGES, createStrategyForm, fillStrategyForm, strategyFromForm } from './strategy-form';

/** Страница редактора стратегии: `/strategies/new` и `/strategies/:id`. */
@Component({
  selector: 'sim-strategy-page',
  imports: [
    ReactiveFormsModule, RouterLink, MatButtonModule, MatExpansionModule, MatFormFieldModule, MatIconModule,
    MatInputModule, MatProgressBarModule, PageHeaderComponent, EmptyStateComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './strategy-page.component.html',
  styleUrl: './strategy-page.component.scss',
})
export class StrategyPageComponent {
  /** `:id` из маршрута; на `/strategies/new` не задан. */
  readonly id = input<string | undefined>();

  private readonly api = inject(StrategiesApi);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly playbookStages = PLAYBOOK_STAGES;
  protected readonly form = createStrategyForm();

  protected readonly strategies = signal<Strategy[]>([]);
  protected readonly ready = signal(false);
  protected readonly saving = signal(false);

  protected readonly strategy = computed(() => {
    const id = Number(this.id());
    return this.id() ? (this.strategies().find((s) => s.id === id) ?? null) : null;
  });
  protected readonly missing = computed(() => !!this.id() && this.strategy() === null);

  constructor() {
    this.api
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((list) => {
        this.strategies.set(list);
        fillStrategyForm(this.form, this.strategy());
        this.ready.set(true);
      });
  }

  protected addRule(): void {
    this.form.controls.rules.push(new FormControl('', { nonNullable: true }));
  }

  protected removeRule(i: number): void {
    this.form.controls.rules.removeAt(i);
    this.form.markAsDirty();
  }

  protected save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const original = this.strategy();
    const body = strategyFromForm(this.form, original);
    this.saving.set(true);
    (original ? this.api.update(original.id, body) : this.api.create(body))
      .pipe(
        finalize(() => this.saving.set(false)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((saved) => {
        this.strategies.update((list) =>
          list.some((x) => x.id === saved.id) ? list.map((x) => (x.id === saved.id ? saved : x)) : [...list, saved],
        );
        fillStrategyForm(this.form, saved);
        this.toast.info('Стратегия сохранена');
        if (this.id() !== String(saved.id)) void this.router.navigate(['/strategies', saved.id], { replaceUrl: true });
      });
  }

  protected remove(): void {
    const sel = this.strategy();
    if (!sel) return;
    this.confirm
      .confirm({ title: 'Удалить стратегию?', message: `«${sel.name}» будет удалена.`, confirmLabel: 'Удалить', danger: true })
      .pipe(
        switchMap(() => this.api.remove(sel.id)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => void this.router.navigate(['/strategies']));
  }
}
