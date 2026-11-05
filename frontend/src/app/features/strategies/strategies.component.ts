import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError } from 'rxjs';
import { StrategiesApi } from '../../core/api/strategies.api';
import { Strategy } from '../../core/models';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';

/** Список стратегий; редактор — отдельная страница `/strategies/new` и `/strategies/:id`. */
@Component({
  selector: 'sim-strategies',
  imports: [RouterLink, MatButtonModule, MatIconModule, PageHeaderComponent, EmptyStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './strategies.component.html',
  styleUrl: './strategies.component.scss',
})
export class StrategiesComponent {
  private readonly api = inject(StrategiesApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly strategies = signal<Strategy[]>([]);
  protected readonly loaded = signal(false);

  constructor() {
    this.api
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((s) => {
        this.strategies.set(s);
        this.loaded.set(true);
      });
  }
}
