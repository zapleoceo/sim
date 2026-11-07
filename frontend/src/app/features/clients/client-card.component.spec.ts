import { HttpErrorResponse } from '@angular/common/http';
import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideNativeDateAdapter } from '@angular/material/core';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { ActivityApi } from '../../core/api/activity.api';
import { ChannelsApi } from '../../core/api/channels.api';
import { ClientsApi } from '../../core/api/clients.api';
import { PersonasApi } from '../../core/api/personas.api';
import { AuthService } from '../../core/auth.service';
import { ClientCard } from '../../core/models';
import { ClientCardComponent } from './client-card.component';

const CLIENT: ClientCard = {
  id: 1, display_name: 'Client', avatar_url: '', persona_id: 1, stage: 'engaged', status: 'active',
  next_step: null, next_step_at: null, notes: null, needs: [], channels: [], last_contact_at: null,
  created_at: '2026-11-07T10:00:00Z', events: [], partner_events: [],
};

describe('ClientCard communication integration', () => {
  let clients: jasmine.SpyObj<ClientsApi>;
  beforeEach(() => {
    clients = jasmine.createSpyObj<ClientsApi>('ClientsApi', ['get', 'patch', 'changeStage']);
    clients.get.and.returnValue(of(CLIENT));
    TestBed.configureTestingModule({ providers: [provideRouter([]), provideNoopAnimations(), provideNativeDateAdapter(),
      { provide: ClientsApi, useValue: clients },
      { provide: ActivityApi, useValue: { messages: () => of({ items: [], total: 0, limit: 25, offset: 0 }) } },
      { provide: AuthService, useValue: { isOwner: () => true } },
      { provide: ChannelsApi, useValue: { list: () => of([]) } }, { provide: PersonasApi, useValue: { list: () => of([]) } },
    ] });
  });
  it('shows a truthful not-found state and retries without losing navigation', fakeAsync(() => {
    clients.get.and.returnValue(throwError(() => new HttpErrorResponse({ status: 404 })));
    const fixture = TestBed.createComponent(ClientCardComponent); fixture.componentRef.setInput('id', '1'); fixture.detectChanges(); tick(); fixture.detectChanges();
    const host: HTMLElement = fixture.nativeElement;
    expect(host.textContent).toContain('Клиент не найден'); expect(host.textContent).not.toContain('Загружаем клиента');
    expect(host.querySelector('a.back')?.getAttribute('href')).toBe('/clients');
    clients.get.and.returnValue(of(CLIENT));
    Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes('Повторить'))?.click();
    fixture.detectChanges(); tick(300); fixture.detectChanges();
    expect(host.textContent).toContain('Работа с клиентом'); expect(host.textContent).toContain('Лента общения');
    fixture.destroy();
  }));
  it('keeps all existing sections before conversation and jump button scrolls without navigation', fakeAsync(() => {
    const fixture = TestBed.createComponent(ClientCardComponent); fixture.componentRef.setInput('id', '1'); fixture.detectChanges(); tick(300); fixture.detectChanges();
    const host: HTMLElement = fixture.nativeElement;
    const history = host.querySelector('[aria-labelledby="hist-h"]'); const feed = host.querySelector<HTMLElement>('#conversation');
    expect(history).not.toBeNull(); expect(feed).not.toBeNull();
    expect(Boolean(history && feed && (history.compareDocumentPosition(feed) & Node.DOCUMENT_POSITION_FOLLOWING))).toBeTrue();
    if (!feed) throw new Error('Missing conversation section');
    const scroll = spyOn(feed, 'scrollIntoView');
    Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes('Перейти к переписке'))?.click();
    expect(scroll).toHaveBeenCalledWith({ block: 'start' });
    fixture.destroy();
  }));
});
