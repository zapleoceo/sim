import { TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of } from 'rxjs';
import { ChannelsApi } from '../../core/api/channels.api';
import { Channel, ConnectState } from '../../core/models';
import { ChannelWizardComponent } from './channel-wizard.component';

const CHANNEL: Channel = {
  id: 7, kind: 'telegram_user', name: 'Аккаунт', persona_id: 1, status: 'new', account_label: '', last_error: null,
  last_activity_at: null,
};

describe('ChannelWizardComponent (telegram_user)', () => {
  let api: jasmine.SpyObj<ChannelsApi>;

  function setup(): { el: HTMLElement; click: (selector: string) => void; fill: (selector: string, v: string) => void; fixture: ReturnType<typeof TestBed.createComponent<ChannelWizardComponent>> } {
    api = jasmine.createSpyObj<ChannelsApi>('ChannelsApi', ['create', 'connect', 'verify', 'state']);
    TestBed.configureTestingModule({
      imports: [ChannelWizardComponent, NoopAnimationsModule],
      providers: [
        { provide: ChannelsApi, useValue: api },
        { provide: MAT_DIALOG_DATA, useValue: { personas: [], channel: CHANNEL } },
        { provide: MatDialogRef, useValue: { close: jasmine.createSpy('close') } },
      ],
    });
    const fixture = TestBed.createComponent(ChannelWizardComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    return {
      el, fixture,
      click: (selector) => { el.querySelector<HTMLElement>(selector)?.click(); fixture.detectChanges(); },
      fill: (selector, v) => {
        const input = el.querySelector<HTMLInputElement>(selector) as HTMLInputElement;
        input.value = v;
        input.dispatchEvent(new Event('input'));
        fixture.detectChanges();
      },
    };
  }

  it('shows the instructions and sends api_id as a string with the phone', () => {
    const t = setup();
    expect(t.el.textContent).toContain('my.telegram.org');
    expect(t.el.textContent).toContain('API development tools');
    api.connect.and.returnValue(of<ConnectState>({ status: 'challenge', need: 'code', message: 'Введите код' }));
    t.fill('[formControlName=api_id]', '12345');
    t.fill('[formControlName=api_hash]', 'a'.repeat(32));
    t.fill('[formControlName=phone]', '+380501234567');
    t.el.querySelector('form#connect')?.dispatchEvent(new Event('submit'));
    t.fixture.detectChanges();
    expect(api.connect).toHaveBeenCalledWith(7, { api_id: '12345', api_hash: 'a'.repeat(32), phone: '+380501234567' });
    expect(t.el.querySelector('form#code')).not.toBeNull();
  });

  it('does not call the API while the form is invalid', () => {
    const t = setup();
    t.fill('[formControlName=api_id]', 'abc');
    t.el.querySelector('form#connect')?.dispatchEvent(new Event('submit'));
    expect(api.connect).not.toHaveBeenCalled();
  });

  it('moves code -> 2FA password step (masked) -> done, sending the password through verify', () => {
    const t = setup();
    api.connect.and.returnValue(of<ConnectState>({ status: 'challenge', need: 'code' }));
    t.fill('[formControlName=api_id]', '1');
    t.fill('[formControlName=api_hash]', 'b'.repeat(32));
    t.fill('[formControlName=phone]', '+380501234567');
    t.el.querySelector('form#connect')?.dispatchEvent(new Event('submit'));
    t.fixture.detectChanges();

    api.verify.and.returnValue(of<ConnectState>({ status: 'challenge', need: '2fa' }));
    t.fill('form#code [formControlName=code]', '12345');
    t.el.querySelector('form#code')?.dispatchEvent(new Event('submit'));
    t.fixture.detectChanges();
    expect(api.verify).toHaveBeenCalledWith(7, '12345');
    const password = t.el.querySelector<HTMLInputElement>('form#password input');
    expect(password?.type).toBe('password');

    api.verify.and.returnValue(of<ConnectState>({ status: 'active' }));
    t.fill('form#password input', ' my pass ');
    t.el.querySelector('form#password')?.dispatchEvent(new Event('submit'));
    t.fixture.detectChanges();
    expect(api.verify).toHaveBeenCalledWith(7, ' my pass ');
    expect(t.el.textContent).toContain('Канал подключён');
  });
});
