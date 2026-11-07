import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { Message } from '../core/models';
import { formatInt, formatMoney } from './format';

export function metric(value: number | null | undefined): string { return value == null ? '—' : formatInt(value); }

@Component({
  selector: 'sim-message-metadata',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (message().author === 'operator') { <span>Оператор: {{ message().operator_name || 'имя не сохранено' }}</span> }
    @if (message().author === 'ai') {
      <span>Генератор: {{ message().model || 'модель не сохранена' }}</span>
      <span>Провайдер: {{ message().provider || '—' }}</span>
      <span class="sim-num">Токены: вход {{ tokensIn() }} · выход {{ tokensOut() }}</span>
      <span class="sim-num">Стоимость: {{ cost() }}</span>
    }
  `,
  styles: `:host { display: flex; flex-wrap: wrap; gap: var(--sim-space-1) var(--sim-space-3); color: var(--sim-text-muted); font-size: 12px; overflow-wrap: anywhere; }`,
})
export class MessageMetadataComponent {
  readonly message = input.required<Message>();
  protected readonly tokensIn = computed(() => metric(this.message().tokens_in));
  protected readonly tokensOut = computed(() => metric(this.message().tokens_out));
  protected readonly cost = computed(() => {
    const cost = this.message().cost_usd;
    return cost == null ? '—' : formatMoney(cost, 'USD', 6);
  });
}
