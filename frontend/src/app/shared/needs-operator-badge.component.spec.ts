import { TestBed } from '@angular/core/testing';
import { NeedsOperatorBadgeComponent } from './needs-operator-badge.component';

describe('NeedsOperatorBadgeComponent', () => {
  it('shows the "нужен оператор" label', () => {
    const fixture = TestBed.createComponent(NeedsOperatorBadgeComponent);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent?.trim()).toBe('нужен оператор');
  });
});
