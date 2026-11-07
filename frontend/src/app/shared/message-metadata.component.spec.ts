import { TestBed } from '@angular/core/testing';
import { Message } from '../core/models';
import { MessageMetadataComponent } from './message-metadata.component';

const MESSAGE: Message = { id: 1, direction: 'out', author: 'ai', text: 'test', created_at: '2026-11-07T10:00:00Z', delivery: 'sent' };
describe('message metadata provenance', () => {
  it('distinguishes zero from missing token data and labels generator metrics', () => {
    const fixture = TestBed.createComponent(MessageMetadataComponent);
    fixture.componentRef.setInput('message', { ...MESSAGE, model: 'model-a', tokens_in: 0, tokens_out: null, cost_usd: 0 });
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Генератор: model-a');
    expect(text).toContain('вход 0'); expect(text).toContain('выход —');
    expect(text).toContain('0,000000');
  });
  it('does not invent historical operator identity or model usage', () => {
    const fixture = TestBed.createComponent(MessageMetadataComponent);
    fixture.componentRef.setInput('message', { ...MESSAGE, author: 'operator', operator_name: null });
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('имя не сохранено'); expect(text).not.toContain('Токены');
    fixture.componentRef.setInput('message', { ...MESSAGE, author: 'operator', operator_name: 'Анна' });
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Оператор: Анна');
  });
});
