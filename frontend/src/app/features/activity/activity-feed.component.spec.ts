import { signal } from '@angular/core';
import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { MatPaginator } from '@angular/material/paginator';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { ActivityApi } from '../../core/api/activity.api';
import { ChannelsApi } from '../../core/api/channels.api';
import { PersonasApi } from '../../core/api/personas.api';
import { ActivityPage, MessageActivity } from '../../core/activity.models';
import { AuthService } from '../../core/auth.service';
import { ActivityComponent } from '../activity.component';
import { ActivityFeedComponent } from './activity-feed.component';
import { ActivityFiltersComponent } from './activity-filters.component';

const message = (id: number): MessageActivity => ({
  id, dialog_id: 4, direction: 'in', author: 'client', text: `message-${id}`, created_at: `2026-11-07T10:00:00Z`, delivery: 'sent', sent_at: null,
  client: { id: 1, display_name: 'Client', avatar_url: '' }, persona: { id: 1, name: 'Persona' },
  channel: { id: 2, name: 'Channel', kind: 'telegram', account_label: null },
});
const page: ActivityPage<MessageActivity> = { items: [message(3), message(2), message(1)], total: 70, limit: 25, offset: 0 };

describe('activity feed', () => {
  let api: jasmine.SpyObj<ActivityApi>;
  const owner = signal(true);
  beforeEach(() => {
    owner.set(true);
    api = jasmine.createSpyObj<ActivityApi>('ActivityApi', ['messages', 'llm']);
    api.messages.and.returnValue(of(page));
    api.llm.and.returnValue(of({ items: [], total: 0, limit: 25, offset: 0 }));
    TestBed.configureTestingModule({ providers: [provideRouter([]), provideNoopAnimations(),
      { provide: ActivityApi, useValue: api }, { provide: AuthService, useValue: { isOwner: owner } },
      { provide: ChannelsApi, useValue: { list: () => of([]) } }, { provide: PersonasApi, useValue: { list: () => of([]) } },
    ] });
  });
  function settle(fixture: ComponentFixture<unknown>): void { fixture.detectChanges(); tick(300); fixture.detectChanges(); tick(); fixture.detectChanges(); }

  it('renders newest-page client history chronologically, resets paging on filters and refresh', fakeAsync(() => {
    const fixture = TestBed.createComponent(ActivityFeedComponent);
    fixture.componentRef.setInput('clientId', 1); settle(fixture);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text.indexOf('message-1')).toBeLessThan(text.indexOf('message-3'));
    expect(api.messages.calls.mostRecent().args[2]).toBe(1);
    const paginator = fixture.debugElement.query(By.directive(MatPaginator)).componentInstance as MatPaginator;
    paginator.page.emit({ pageIndex: 2, pageSize: 25, length: 70 }); settle(fixture);
    expect(api.messages.calls.mostRecent().args[1]?.offset).toBe(50);
    const filters = fixture.debugElement.query(By.directive(ActivityFiltersComponent)).componentInstance as ActivityFiltersComponent;
    filters.changed.emit({ q: 'new', direction: 'out' }); settle(fixture);
    expect(api.messages.calls.mostRecent().args[0]).toEqual({ q: 'new', direction: 'out' });
    expect(api.messages.calls.mostRecent().args[1]?.offset).toBe(0);
    const refresh = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button')).find((b) => b.textContent?.includes('Обновить'));
    refresh?.click(); settle(fixture);
    expect(api.messages.calls.mostRecent().args[0].q).toBe('new');
    fixture.destroy();
  }));

  it('shows retryable error instead of empty success and recovers', fakeAsync(() => {
    api.messages.and.returnValue(throwError(() => new Error('secret-provider-body')));
    const fixture = TestBed.createComponent(ActivityFeedComponent); settle(fixture);
    let text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Не удалось загрузить'); expect(text).not.toContain('secret-provider-body');
    expect(text).not.toContain('Сообщений не найдено');
    api.messages.and.returnValue(of({ ...page, items: [], total: 0 }));
    Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button')).find((b) => b.textContent?.includes('Повторить'))?.click();
    settle(fixture); text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Сообщений не найдено'); expect(text).not.toContain('Не удалось загрузить');
    fixture.destroy();
  }));

  it('cancels stale request subscription and does not request with invalid filters', fakeAsync(() => {
    const stale = new Subject<ActivityPage<MessageActivity>>(); api.messages.and.returnValue(stale);
    const fixture = TestBed.createComponent(ActivityFeedComponent); settle(fixture);
    expect(stale.observed).toBeTrue();
    const filters = fixture.debugElement.query(By.directive(ActivityFiltersComponent)).componentInstance as ActivityFiltersComponent;
    filters.changed.emit(null); settle(fixture);
    expect(stale.observed).toBeFalse();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Исправьте фильтры');
    api.messages.and.returnValue(of({ ...page, items: [message(8)] }));
    filters.changed.emit({ q: 'current' }); settle(fixture);
    stale.next(page); fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('message-8'); expect(text).not.toContain('message-3'); fixture.destroy();
  }));

  it('never requests owner-only calls for an operator, even with a direct llm input', fakeAsync(() => {
    owner.set(false);
    const fixture = TestBed.createComponent(ActivityFeedComponent); fixture.componentRef.setInput('llm', true); settle(fixture);
    expect(api.llm).not.toHaveBeenCalled(); expect(api.messages).not.toHaveBeenCalled(); fixture.destroy();
  }));

  it('ignores an operator llm deep link and hides the call tab', fakeAsync(() => {
    owner.set(false);
    const fixture = TestBed.createComponent(ActivityComponent); fixture.componentRef.setInput('tab', 'llm'); settle(fixture);
    expect(api.llm).not.toHaveBeenCalled(); expect(api.messages).toHaveBeenCalled();
    const tabs = fixture.debugElement.queryAll(By.css('a[mat-tab-link]'));
    expect(tabs.length).toBe(1); fixture.destroy();
  }));

  it('requests calls for owner and preserves a channel deep link on initial request', fakeAsync(() => {
    const fixture = TestBed.createComponent(ActivityComponent);
    fixture.componentRef.setInput('tab', 'llm'); fixture.componentRef.setInput('channel_id', '8'); settle(fixture);
    expect(api.messages).not.toHaveBeenCalled();
    expect(api.llm.calls.allArgs().every(([filter]) => filter.channel_id === 8)).toBeTrue();
    expect(api.llm).toHaveBeenCalled(); fixture.destroy();
  }));
});
