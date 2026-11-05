import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError } from 'rxjs';
import { PersonasApi } from '../../core/api/personas.api';
import { Persona } from '../../core/models';
import { AvatarComponent } from '../../shared/avatar.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';

@Component({
  selector: 'sim-personas',
  imports: [RouterLink, MatButtonModule, MatIconModule, PageHeaderComponent, AvatarComponent, EmptyStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-page">
      <sim-page-header title="Персонажи" subtitle="Виртуальные представители, которые ведут диалоги">
        <a mat-flat-button routerLink="/personas/new">
          <mat-icon class="material-icons-outlined">add</mat-icon>
          Новый персонаж
        </a>
      </sim-page-header>

      <div class="cards">
        @for (p of personas(); track p.id) {
          <a class="sim-card persona" [routerLink]="['/personas', p.id]">
            <sim-avatar [name]="p.name" [url]="p.avatar_url" [size]="56" />
            <span class="info">
              <strong>{{ p.name }}@if (p.age) {, {{ p.age }}}</strong>
              <span class="sim-muted tagline">{{ p.description }}</span>
              <span class="meta">
                <span class="pill" [class.off]="!p.is_active">{{ p.is_active ? 'Активен' : 'Выключен' }}</span>
                <span class="sim-muted">{{ p.model || 'авто' }}</span>
              </span>
            </span>
          </a>
        } @empty {
          @if (loaded()) {
            <sim-empty-state icon="face" title="Персонажей пока нет" hint="Создайте первого — он будет вести диалоги в каналах." />
          }
        }
      </div>
    </div>
  `,
  styles: `
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr)); gap: var(--sim-space-3); }
    .persona { display: flex; gap: var(--sim-space-3); color: inherit; text-decoration: none; align-items: center; min-width: 0; }
    .persona:hover { background: var(--sim-surface-2); }
    .info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .tagline { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .meta { display: flex; gap: var(--sim-space-2); align-items: center; font-size: 12px; min-width: 0; }
    .pill { padding: 0 8px; border-radius: 6px; background: color-mix(in srgb, var(--sim-ok) 14%, transparent); color: var(--sim-ok); }
    .pill.off { background: var(--sim-surface-2); color: var(--sim-text-muted); }
  `,
})
export class PersonasComponent {
  private readonly personasApi = inject(PersonasApi);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly personas = signal<Persona[]>([]);
  protected readonly loaded = signal(false);

  constructor() {
    this.personasApi
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((p) => {
        this.personas.set(p);
        this.loaded.set(true);
      });
  }
}
