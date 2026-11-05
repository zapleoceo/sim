import { Routes } from '@angular/router';
import { authGuard, ownerGuard } from './core/guards';

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
      { path: '', pathMatch: 'full', redirectTo: 'inbox' },
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
        path: 'strategies',
        canActivate: [ownerGuard],
        title: 'Стратегии — Sim',
        loadComponent: () => import('./features/strategies/strategies.component').then((m) => m.StrategiesComponent),
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
    ],
  },
  { path: '**', redirectTo: '' },
];
