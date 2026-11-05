import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { EMPTY, catchError, finalize, switchMap } from 'rxjs';
import { StrategiesApi } from '../../core/api/strategies.api';
import { Strategy } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { ConfirmService } from '../../shared/confirm-dialog.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { PLAYBOOK_STAGES, createStrategyForm, fillStrategyForm, strategyFromForm } from './strategy-form';
import { FormControl } from '@angular/forms';

type Selection = Strategy | 'new' | null;

@Component({
  selector: 'sim-strategies',
  imports: [
    ReactiveFormsModule, MatButtonModule, MatExpansionModule, MatFormFieldModule, MatIconModule, MatInputModule,
    PageHeaderComponent, EmptyStateComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './strategies.component.html',
  styleUrl: './strategies.component.scss',
})
export class StrategiesComponent {
  private readonly api = inject(StrategiesApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly playbookStages = PLAYBOOK_STAGES;
  protected readonly form = createStrategyForm();

  protected readonly strategies = signal<Strategy[]>([]);
  protected readonly selected = signal<Selection>(null);
  protected readonly loaded = signal(false);
  protected readonly saving = signal(false);

  constructor() {
    this.api
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((s) => {
        this.strategies.set(s);
        this.loaded.set(true);
      });
  }

  protected select(s: Selection): void {
    this.selected.set(s);
    fillStrategyForm(this.form, s === 'new' ? null : s);
  }

  protected isSelected(s: Strategy): boolean {
    const sel = this.selected();
    return sel !== null && sel !== 'new' && sel.id === s.id;
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
    const sel = this.selected();
    const original = sel && sel !== 'new' ? sel : null;
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
        this.select(saved);
        this.toast.info('Стратегия сохранена');
      });
  }

  protected remove(): void {
    const sel = this.selected();
    if (!sel || sel === 'new') return;
    this.confirm
      .confirm({ title: 'Удалить стратегию?', message: `«${sel.name}» будет удалена.`, confirmLabel: 'Удалить', danger: true })
      .pipe(
        switchMap(() => this.api.remove(sel.id)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.strategies.update((list) => list.filter((x) => x.id !== sel.id));
        this.selected.set(null);
      });
  }
}
