import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSidenavModule } from '@angular/material/sidenav';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/auth.service';
import { AvatarComponent } from '../shared/avatar.component';
import { RAIL_QUERY, injectIsPhone, injectMediaQuery } from '../shared/breakpoints';
import { ROLE_LABELS, visibleNav } from './nav';

const BOTTOM_COUNT = 4;

@Component({
  selector: 'sim-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MatSidenavModule, MatListModule, MatIconModule, MatButtonModule, MatTooltipModule, AvatarComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './shell.component.html',
  styleUrl: './shell.component.scss',
})
export class ShellComponent {
  private readonly auth = inject(AuthService);

  protected readonly isPhone = injectIsPhone();
  /** Планшет: навигация-рейка только с иконками. */
  protected readonly isRail = injectMediaQuery(RAIL_QUERY);
  protected readonly drawerOpen = signal(false);

  protected readonly user = this.auth.user;
  protected readonly roleLabel = computed(() => ROLE_LABELS[this.user()?.role ?? 'operator']);
  protected readonly userName = computed(() => this.user()?.display_name || this.user()?.tg_username || 'Администратор');
  protected readonly items = computed(() => visibleNav(this.user()?.role));
  protected readonly bottomItems = computed(() => this.items().slice(0, BOTTOM_COUNT));

  protected toggleDrawer(): void {
    this.drawerOpen.update((v) => !v);
  }

  protected closeDrawer(): void {
    this.drawerOpen.set(false);
  }

  protected logout(): void {
    this.closeDrawer();
    this.auth.logout();
  }

}
