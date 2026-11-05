import { BreakpointObserver } from '@angular/cdk/layout';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSidenavModule } from '@angular/material/sidenav';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from '../core/auth.service';
import { Role } from '../core/models';

interface NavItem { path: string; label: string; icon: string; owner?: boolean }

const NAV: readonly NavItem[] = [
  { path: '/inbox', label: 'Диалоги', icon: 'forum' },
  { path: '/clients', label: 'Клиенты', icon: 'groups' },
  { path: '/funnel', label: 'Воронка', icon: 'filter_alt' },
  { path: '/dashboard', label: 'Аналитика', icon: 'insights' },
  { path: '/personas', label: 'Персонажи', icon: 'face', owner: true },
  { path: '/strategies', label: 'Стратегии', icon: 'route', owner: true },
  { path: '/channels', label: 'Каналы', icon: 'cable', owner: true },
  { path: '/content', label: 'Контент', icon: 'photo_library', owner: true },
  { path: '/partner', label: 'Партнёр', icon: 'handshake', owner: true },
];

const ROLE_LABELS: Record<Role, string> = { owner: 'Владелец', operator: 'Оператор' };
const BOTTOM_COUNT = 4;

@Component({
  selector: 'sim-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MatSidenavModule, MatIconModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './shell.component.html',
  styleUrl: './shell.component.scss',
})
export class ShellComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly isPhone = toSignal(
    inject(BreakpointObserver)
      .observe('(max-width: 767px)')
      .pipe(map((s) => s.matches)),
    { initialValue: false },
  );
  protected readonly drawerOpen = signal(false);

  protected readonly user = this.auth.user;
  protected readonly roleLabel = computed(() => ROLE_LABELS[this.user()?.role ?? 'operator']);
  protected readonly items = computed(() => NAV.filter((i) => !i.owner || this.auth.isOwner()));
  protected readonly bottomItems = computed(() => this.items().slice(0, BOTTOM_COUNT));
  protected readonly hasMore = computed(() => this.items().length > BOTTOM_COUNT);

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

  protected go(path: string): void {
    this.closeDrawer();
    void this.router.navigateByUrl(path);
  }
}
