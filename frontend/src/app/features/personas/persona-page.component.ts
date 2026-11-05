import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, forkJoin, of } from 'rxjs';
import { PersonasApi } from '../../core/api/personas.api';
import { StrategiesApi } from '../../core/api/strategies.api';
import { BrokerModel, Persona, Strategy } from '../../core/models';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { PersonaEditorComponent } from './persona-editor.component';

/** Страница редактора персонажа: `/personas/new` и `/personas/:id`. */
@Component({
  selector: 'sim-persona-page',
  imports: [RouterLink, MatButtonModule, MatIconModule, MatProgressBarModule, PageHeaderComponent, EmptyStateComponent, PersonaEditorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (!ready()) {
      <mat-progress-bar class="top-bar" mode="indeterminate" aria-label="Загрузка" />
    }
    <div class="sim-page sim-page--narrow">
      <a mat-button routerLink="/personas" class="back">
        <mat-icon class="material-icons-outlined">arrow_back</mat-icon>
        Все персонажи
      </a>
      @if (ready()) {
        <sim-page-header [title]="persona() ? persona()!.name : (missing() ? 'Персонаж' : 'Новый персонаж')" [subtitle]="persona() ? 'Редактирование' : 'Заполните карточку и сохраните'" />
        @if (missing()) {
          <sim-empty-state icon="face" title="Персонаж не найден" hint="Возможно, он был удалён." />
        } @else {
          <sim-persona-editor
            [persona]="persona()"
            [models]="models()"
            [modelsUnavailable]="modelsUnavailable()"
            [strategies]="strategies()"
            (saved)="onSaved($event)"
            (removed)="onRemoved()"
          />
        }
      }
    </div>
  `,
  styles: `
    .top-bar { position: sticky; top: 0; z-index: 5; height: 3px; }
    .back { align-self: flex-start; margin-left: -12px; }
  `,
})
export class PersonaPageComponent {
  /** `:id` из маршрута; на `/personas/new` не задан. */
  readonly id = input<string | undefined>();

  private readonly personasApi = inject(PersonasApi);
  private readonly strategiesApi = inject(StrategiesApi);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly personas = signal<Persona[]>([]);
  protected readonly strategies = signal<Strategy[]>([]);
  protected readonly models = signal<BrokerModel[]>([]);
  protected readonly modelsUnavailable = signal(false);
  protected readonly ready = signal(false);

  protected readonly persona = computed(() => {
    const id = Number(this.id());
    return this.id() ? (this.personas().find((p) => p.id === id) ?? null) : null;
  });
  protected readonly missing = computed(() => !!this.id() && this.persona() === null);

  constructor() {
    forkJoin({
      personas: this.personasApi.list(),
      strategies: this.strategiesApi.list().pipe(catchError(() => of([] as Strategy[]))),
      // Список моделей — вспомогательный: ошибка не должна давать тост, только подсказку у поля.
      models: this.personasApi.models().pipe(
        catchError(() => {
          this.modelsUnavailable.set(true);
          return of([] as BrokerModel[]);
        }),
      ),
    })
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((r) => {
        this.personas.set(r.personas);
        this.strategies.set(r.strategies);
        this.models.set(r.models);
        this.ready.set(true);
      });
  }

  protected onSaved(p: Persona): void {
    this.personas.update((list) => (list.some((x) => x.id === p.id) ? list.map((x) => (x.id === p.id ? p : x)) : [...list, p]));
    if (this.id() !== String(p.id)) void this.router.navigate(['/personas', p.id], { replaceUrl: true });
  }

  protected onRemoved(): void {
    void this.router.navigate(['/personas']);
  }
}
