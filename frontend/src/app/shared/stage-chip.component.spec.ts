import { TestBed } from '@angular/core/testing';
import { STAGES, stageMeta } from '../core/stages';
import { StageChipComponent } from './stage-chip.component';

describe('stage mapping', () => {
  it('has a label and a distinct colour for each of the 7 stages', () => {
    expect(STAGES.length).toBe(7);
    expect(new Set(STAGES.map((s) => s.color)).size).toBe(7);
    expect(STAGES.every((s) => s.label.length > 0)).toBeTrue();
  });

  it('marks only registered and paid as partner-only', () => {
    const locked = STAGES.filter((s) => !s.aiReachable).map((s) => s.id);
    expect(locked).toEqual(['registered', 'paid']);
  });

  it('falls back gracefully for an unknown stage', () => {
    const m = stageMeta('something_new');
    expect(m.label).toBe('something_new');
    expect(m.aiReachable).toBeFalse();
  });
});

describe('StageChipComponent', () => {
  it('renders the Russian label and stage colour', () => {
    const fixture = TestBed.createComponent(StageChipComponent);
    fixture.componentRef.setInput('stage', 'handed_to_partner');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('У партнёра');
    expect(el.style.getPropertyValue('--chip-color')).toBe('var(--sim-stage-5)');
  });
});
