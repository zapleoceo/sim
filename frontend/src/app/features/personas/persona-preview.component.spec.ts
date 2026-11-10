import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { PersonasApi } from '../../core/api/personas.api';
import { PersonaPreviewComponent } from './persona-preview.component';

describe('PersonaPreviewComponent', () => {
  let preview: jasmine.Spy;

  beforeEach(() => {
    preview = jasmine.createSpy('preview').and.returnValue(
      of({ reply: 'ok', model: 'm', latency_ms: 1, decision: null, guard: null }),
    );
    TestBed.configureTestingModule({
      imports: [PersonaPreviewComponent],
      providers: [{ provide: PersonasApi, useValue: { preview } }],
    });
  });

  it('sends the message on submit without a native page submit', () => {
    const fixture = TestBed.createComponent(PersonaPreviewComponent);
    fixture.componentRef.setInput('personaId', 1);
    fixture.detectChanges();

    const form: HTMLFormElement = fixture.nativeElement.querySelector('form');
    const input: HTMLInputElement = fixture.nativeElement.querySelector('input');
    input.value = 'привет';
    input.dispatchEvent(new Event('input'));

    const submit = new Event('submit', { cancelable: true });
    form.dispatchEvent(submit);

    expect(submit.defaultPrevented).toBeTrue();
    expect(preview).toHaveBeenCalledOnceWith(1, 'привет', []);
  });
});
