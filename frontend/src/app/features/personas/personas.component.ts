import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { EMPTY, catchError, forkJoin, of } from 'rxjs';
import { PersonasApi } from '../../core/api/personas.api';
import { StrategiesApi } from '../../core/api/strategies.api';
import { BrokerModel, Persona, Strategy } from '../../core/models';
import { AvatarComponent } from '../../shared/avatar.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { PersonaEditorComponent } from './persona-editor.component';

type Selection = Persona | 'new' | null;

@Component({
  selector: 'sim-personas',
  imports: [MatButtonModule, MatIconModule, PageHeaderComponent, AvatarComponent, EmptyStateComponent, PersonaEditorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page">
      <sim-page-header title="Персонажи" subtitle="Виртуальные представители, которые ведут диалоги">
        <button mat-flat-button type="button" (click)="selected.set('new')">
          <mat-icon class="material-icons-outlined">add</mat-icon>
          Новый персонаж
        </button>
      </sim-page-header>

      <div class="layout" [class.editing]="selected() !== null">
        <div class="cards">
          @for (p of personas(); track p.id) {
            <button type="button" class="sim-card persona" [class.active]="isSelected(p)" (click)="selected.set(p)">
              <sim-avatar [name]="p.name" [url]="p.avatar_url" [size]="56" />
              <span class="info">
                <strong>{{ p.name }}@if (p.age) {, {{ p.age }}}</strong>
                <span class="sim-muted tagline">{{ p.tagline }}</span>
                <span class="meta">
                  <span class="pill" [class.off]="!p.is_active">{{ p.is_active ? 'Активен' : 'Выключен' }}</span>
                  <span class="sim-muted">{{ p.model || 'chat:sales' }}</span>
                </span>
              </span>
            </button>
          } @empty {
            @if (loaded()) {
              <sim-empty-state icon="face" title="Персонажей пока нет" hint="Создайте первого — он будет вести диалоги в каналах." />
            }
          }
        </div>

        @if (selected(); as sel) {
          <div class="editor">
            <sim-persona-editor
              [persona]="sel === 'new' ? null : sel"
              [models]="models()"
              [strategies]="strategies()"
              (saved)="onSaved($event)"
              (removed)="onRemoved($event)"
              (closed)="selected.set(null)"
            />
          </div>
        }
      </div>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--sim-space-4); align-items: start; }
    .layout.editing { grid-template-columns: minmax(260px, 360px) minmax(0, 1fr); }
    .cards { display: flex; flex-direction: column; gap: var(--sim-space-3); }
    .layout:not(.editing) .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
    .persona { display: flex; gap: var(--sim-space-3); text-align: left; font: inherit; color: inherit; cursor: pointer; align-items: center; }
    .persona:hover { background: var(--sim-surface-2); }
    .persona.active { border-color: var(--sim-accent); }
    .info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .tagline { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .meta { display: flex; gap: var(--sim-space-2); align-items: center; font-size: 12px; }
    .pill { padding: 0 8px; border-radius: 6px; background: color-mix(in srgb, var(--sim-ok) 14%, transparent); color: var(--sim-ok); }
    .pill.off { background: var(--sim-surface-2); color: var(--sim-text-muted); }
    @media (max-width: 899px) {
      .layout.editing { grid-template-columns: minmax(0, 1fr); }
      .layout.editing .cards { display: none; }
    }
  `,
})
export class PersonasComponent {
  private readonly personasApi = inject(PersonasApi);
  private readonly strategiesApi = inject(StrategiesApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly personas = signal<Persona[]>([]);
  protected readonly strategies = signal<Strategy[]>([]);
  protected readonly models = signal<BrokerModel[]>([]);
  protected readonly selected = signal<Selection>(null);
  protected readonly loaded = signal(false);

  protected readonly selectedId = computed(() => {
    const s = this.selected();
    return s && s !== 'new' ? s.id : null;
  });

  constructor() {
    forkJoin({
      personas: this.personasApi.list(),
      strategies: this.strategiesApi.list().pipe(catchError(() => of([] as Strategy[]))),
      models: this.personasApi.models().pipe(catchError(() => of([] as BrokerModel[]))),
    })
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((r) => {
        this.personas.set(r.personas);
        this.strategies.set(r.strategies);
        this.models.set(r.models);
        this.loaded.set(true);
      });
  }

  protected isSelected(p: Persona): boolean {
    return this.selectedId() === p.id;
  }

  protected onSaved(p: Persona): void {
    this.personas.update((list) => (list.some((x) => x.id === p.id) ? list.map((x) => (x.id === p.id ? p : x)) : [...list, p]));
    this.selected.set(p);
  }

  protected onRemoved(id: number): void {
    this.personas.update((list) => list.filter((p) => p.id !== id));
    this.selected.set(null);
  }
}
