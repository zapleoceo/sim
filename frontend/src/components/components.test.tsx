import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import type { Message } from '@/lib/models';
import { Avatar } from './avatar';
import { ChannelIcon, StageChip, StatusBadge } from './badges';
import { ConfirmProvider, useConfirm } from './confirm';
import { Field } from './field';
import { GuardBadge } from './guard-badge';
import { MessageContent, MessageMetadata } from './message-parts';
import { decodeOption, type Option } from './option-select';
import { pagerState } from './pager';
import { Input } from './ui/input';

const base: Message = { id: 1, direction: 'out', author: 'ai', text: 'Привет', created_at: '2026-11-11T10:00:00Z', delivery: 'sent' };

describe('shared components', () => {
  it('avatar falls back to initials without a photo', () => {
    render(<Avatar name="Анна Петрова" />);
    expect(screen.getByText('АП')).toBeInTheDocument();
  });

  it('badges label channel, stage and status', () => {
    render(
      <>
        <ChannelIcon kind="whatsapp" />
        <StageChip stage="interested" />
        <StatusBadge status="refused" />
      </>,
    );
    expect(screen.getByRole('img', { name: 'WhatsApp' })).toBeInTheDocument();
    expect(screen.getByText('Интерес')).toBeInTheDocument();
    expect(screen.getByText('Отказ')).toBeInTheDocument();
  });

  it('guard badge shows verdict, violations and the original draft', () => {
    render(<GuardBadge guard={{ verdict: 'blocked', violations: ['обещание дохода'], model: 'g', original_text: 'черновик' }} />);
    expect(screen.getByText('Заблокировано контролером')).toBeInTheDocument();
    expect(screen.getByText('обещание дохода')).toBeInTheDocument();
    expect(screen.getByText('Заблокированный черновик')).toBeInTheDocument();
  });

  it('message content renders text, failed delivery and AI metadata', () => {
    render(
      <>
        <MessageContent message={{ ...base, delivery: 'failed' }} />
        <MessageMetadata message={{ ...base, model: 'gpt', tokens_in: 1200, tokens_out: null, cost_usd: 0.000123 }} />
      </>,
    );
    expect(screen.getByText('Привет')).toBeInTheDocument();
    expect(screen.getByText(/Ошибка отправки/)).toBeInTheDocument();
    expect(screen.getByText('Генератор: gpt')).toBeInTheDocument();
    expect(screen.getByText(/вход 1\s?200 · выход —/)).toBeInTheDocument();
  });

  it('message content shows an attachment label without a url', () => {
    render(<MessageContent message={{ ...base, text: '', media_kind: 'voice', media_url: null }} />);
    expect(screen.getByText('Голосовое')).toBeInTheDocument();
  });

  it('field wires label, hint and error to the control', () => {
    const { rerender } = render(
      <Field label="Имя" hint="Как в паспорте">
        <Input />
      </Field>,
    );
    const input = screen.getByLabelText('Имя');
    expect(input).toHaveAccessibleDescription('Как в паспорте');
    rerender(
      <Field label="Имя" error="Укажите имя">
        <Input />
      </Field>,
    );
    expect(screen.getByLabelText('Имя')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('Укажите имя');
  });

  it('option values round-trip, including null and numbers', () => {
    const options: Option<number | null>[] = [{ value: null, label: 'Все' }, { value: 3, label: 'Марина' }];
    expect(decodeOption('n:3', options)).toBe(3);
    expect(decodeOption('__empty__', options)).toBeNull();
  });

  it('pager math', () => {
    expect(pagerState({ limit: 25, offset: 25 }, 60)).toEqual({ index: 1, size: 25, pages: 3, from: 26, to: 50 });
    expect(pagerState({ limit: 25, offset: 0 }, 0)).toMatchObject({ pages: 1, from: 0, to: 0 });
  });

  it('confirm resolves true on the action and false on cancel', async () => {
    const results: boolean[] = [];
    function Probe() {
      const confirm = useConfirm();
      const [, force] = useState(0);
      return (
        <button
          type="button"
          onClick={async () => {
            results.push(await confirm({ title: 'Удалить?', message: 'Точно', confirmLabel: 'Удалить', danger: true }));
            force((n) => n + 1);
          }}
        >
          go
        </button>
      );
    }
    const user = userEvent.setup();
    render(
      <ConfirmProvider>
        <Probe />
      </ConfirmProvider>,
    );
    await user.click(screen.getByText('go'));
    await user.click(await screen.findByRole('button', { name: 'Удалить' }));
    await user.click(screen.getByText('go'));
    await user.click(await screen.findByRole('button', { name: 'Отмена' }));
    expect(results).toEqual([true, false]);
  });
});
