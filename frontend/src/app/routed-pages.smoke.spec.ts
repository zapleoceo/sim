import { Type } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideNativeDateAdapter } from '@angular/material/core';
import { provideRouter } from '@angular/router';
import { ActivityComponent } from './features/activity.component';
import { AdminsComponent } from './features/admins/admins.component';
import { ChannelsComponent } from './features/channels/channels.component';
import { ClientCardComponent } from './features/clients/client-card.component';
import { ClientsComponent } from './features/clients/clients.component';
import { ContentComponent } from './features/content/content.component';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { FunnelComponent } from './features/funnel/funnel.component';
import { InboxComponent } from './features/inbox/inbox.component';
import { LoginComponent } from './features/login/login.component';
import { PartnerComponent } from './features/partner/partner.component';
import { PersonaPageComponent } from './features/personas/persona-page.component';
import { PersonasComponent } from './features/personas/personas.component';
import { SimulationsComponent } from './features/simulations/simulations.component';
import { StrategyPageComponent } from './features/strategies/strategy-page.component';
import { StrategiesComponent } from './features/strategies/strategies.component';
import { ShellComponent } from './shell/shell.component';

const SUMMARY = {
  contacts: 10, new_contacts: 4, replied: 6, by_stage: { engaged: 3 }, registrations: 2, payments: 1, revenue: 1500,
  partner_payouts: 300, revenue_by_currency: [{ currency: 'USD', total: 1500 }],
  partner_payouts_by_currency: [{ currency: 'USD', total: 300 }], conversion: { contact_to_registration: 0.2, registration_to_payment: 0.5 },
  llm: { calls: 5, cost_usd: 0.12, tokens_in: 100, tokens_out: 50 }, guard: { ok: 3, rewritten: 1, blocked: 0 },
  by_channel: [{ kind: 'telegram', contacts: 10, registrations: 2, payments: 1 }],
  daily: [{ date: '2026-11-01', contacts: 3, registrations: 1, payments: 0 }, { date: '2026-11-02', contacts: 7, registrations: 1, payments: 1 }],
};

function bodyFor(url: string): object | null {
  if (url === '/api/activity/messages') return { items: [], total: 0, limit: 25, offset: 0 };
  if (url === '/api/analytics/summary') return SUMMARY;
  if (url === '/api/funnel') return { stages: [], statuses: { active: 0, paused: 0, no_response: 0, refused: 0 } };
  if (url === '/api/auth/config') return { bot_username: '' };
  if (/^\/api\/(dialogs|clients|simulations|personas|strategies|channels|content|admins|models)$/.test(url)) {
    return { items: [], total: 0 };
  }
  return null; // остальное (карточка клиента и т.п.) остаётся «в загрузке»
}

const PAGES: [string, Type<unknown>, Record<string, unknown>?][] = [
  ['shell', ShellComponent],
  ['login', LoginComponent],
  ['inbox', InboxComponent],
  ['activity', ActivityComponent],
  ['clients', ClientsComponent],
  ['client card', ClientCardComponent, { id: '1' }],
  ['funnel', FunnelComponent],
  ['dashboard', DashboardComponent],
  ['personas', PersonasComponent],
  ['persona new', PersonaPageComponent],
  ['persona edit', PersonaPageComponent, { id: '1' }],
  ['strategies', StrategiesComponent],
  ['strategy new', StrategyPageComponent],
  ['strategy edit', StrategyPageComponent, { id: '1' }],
  ['simulations', SimulationsComponent],
  ['channels', ChannelsComponent],
  ['content', ContentComponent],
  ['partner', PartnerComponent],
  ['admins', AdminsComponent],
];

/**
 * Дымовой тест каждой маршрутизируемой страницы: создаётся, получает ответы API и рендерится без ошибок.
 * Ловит класс багов «в шаблоне matInput/mat-form-field, а модуль не импортирован»
 * (иначе mat-form-field падает на каждом цикле change detection).
 */
describe('routed pages smoke', () => {
  for (const [name, component, inputs] of PAGES) {
    it(`${name} renders without errors`, () => {
      const errors: unknown[][] = [];
      spyOn(console, 'error').and.callFake((...args: unknown[]) => void errors.push(args));
      TestBed.configureTestingModule({
        providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations(), provideNativeDateAdapter(), provideRouter([])],
      });
      const ctrl = TestBed.inject(HttpTestingController);
      const fixture = TestBed.createComponent(component);
      for (const [k, v] of Object.entries(inputs ?? {})) fixture.componentRef.setInput(k, v);
      fixture.detectChanges();

      const flush = (): void => {
        for (const req of ctrl.match(() => true) as TestRequest[]) {
          const body = bodyFor(req.request.url);
          if (body) req.flush(body);
        }
      };
      flush();
      fixture.detectChanges();
      flush();
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).children.length).toBeGreaterThan(0);
      expect(errors).toEqual([]);
      fixture.destroy();
    });
  }
});
