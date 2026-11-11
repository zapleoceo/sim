import { Routes } from '@angular/router';
import { landingGuard } from './core/landing.guard';
import { authGuard, ownerGuard, superadminGuard } from './core/guards';

export const routes: Routes = [
  {
    path: 'login',
    title: 'Вход — Sim',
    loadComponent: () => import('./features/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: '',
    canActivate: [authGuard],
    loadComponent: () => import('./shell/shell.component').then((m) => m.ShellComponent),
    children: [
      { path: '', pathMatch: 'full', canActivate: [landingGuard], loadComponent: () => import('./features/studio/studio.component').then((m) => m.StudioComponent) },
      { path: 'studio', canActivate: [ownerGuard], title: 'Studio - Sim', loadComponent: () => import('./features/studio/studio.component').then((m) => m.StudioComponent) },
      {
        path: 'inbox',
        title: 'Диалоги — Sim',
        loadComponent: () => import('./features/inbox/inbox.component').then((m) => m.InboxComponent),
      },
      {
        path: 'clients',
        title: 'Клиенты — Sim',
        loadComponent: () => import('./features/clients/clients.component').then((m) => m.ClientsComponent),
      },
      {
        path: 'clients/:id',
        title: 'Клиент — Sim',
        loadComponent: () => import('./features/clients/client-card.component').then((m) => m.ClientCardComponent),
      },
      {
        path: 'activity',
        title: 'Активность — Sim',
        loadComponent: () => import('./features/activity.component').then((m) => m.ActivityComponent),
      },
      {
        path: 'funnel',
        title: 'Воронка — Sim',
        loadComponent: () => import('./features/funnel/funnel.component').then((m) => m.FunnelComponent),
      },
      {
        path: 'dashboard',
        title: 'Аналитика — Sim',
        loadComponent: () => import('./features/dashboard/dashboard.component').then((m) => m.DashboardComponent),
      },
      {
        path: 'personas',
        canActivate: [ownerGuard],
        title: 'Персонажи — Sim',
        loadComponent: () => import('./features/personas/personas.component').then((m) => m.PersonasComponent),
      },
      {
        path: 'settings',
        canActivate: [ownerGuard],
        title: 'Промпты и настройки — Sim',
        loadComponent: () => import('./features/settings/settings.component').then((m) => m.SettingsComponent),
      },
      {
        path: 'personas/new',
        canActivate: [ownerGuard],
        title: 'Новый персонаж — Sim',
        loadComponent: () => import('./features/personas/persona-page.component').then((m) => m.PersonaPageComponent),
      },
      {
        path: 'personas/:id',
        canActivate: [ownerGuard],
        title: 'Персонаж — Sim',
        loadComponent: () => import('./features/personas/persona-page.component').then((m) => m.PersonaPageComponent),
      },
      {
        path: 'strategies',
        canActivate: [ownerGuard],
        title: 'Стратегии — Sim',
        loadComponent: () => import('./features/strategies/strategies.component').then((m) => m.StrategiesComponent),
      },
      {
        path: 'strategies/new',
        canActivate: [ownerGuard],
        title: 'Новая стратегия — Sim',
        loadComponent: () => import('./features/strategies/strategy-page.component').then((m) => m.StrategyPageComponent),
      },
      {
        path: 'strategies/:id',
        canActivate: [ownerGuard],
        title: 'Стратегия — Sim',
        loadComponent: () => import('./features/strategies/strategy-page.component').then((m) => m.StrategyPageComponent),
      },
      {
        path: 'simulations',
        canActivate: [ownerGuard],
        title: 'Симуляции — Sim',
        loadComponent: () => import('./features/simulations/simulations.component').then((m) => m.SimulationsComponent),
      },
      {
        path: 'channels',
        canActivate: [ownerGuard],
        title: 'Каналы — Sim',
        loadComponent: () => import('./features/channels/channels.component').then((m) => m.ChannelsComponent),
      },
      {
        path: 'content',
        canActivate: [ownerGuard],
        title: 'Контент — Sim',
        loadComponent: () => import('./features/content/content.component').then((m) => m.ContentComponent),
      },
      {
        path: 'partner',
        canActivate: [ownerGuard],
        title: 'Партнёр — Sim',
        loadComponent: () => import('./features/partner/partner.component').then((m) => m.PartnerComponent),
      },
      {
        path: 'admins',
        canActivate: [superadminGuard],
        title: 'Администраторы — Sim',
        loadComponent: () => import('./features/admins/admins.component').then((m) => m.AdminsComponent),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
