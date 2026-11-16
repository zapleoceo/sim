import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { DialogDetail } from '@/lib/models';
import { ConversationState } from './conversation-state';

const detail = { dialog: { id: 1, ai_paused: true, needs_operator: true,
  reply_plan: { at: '2026-11-16T09:47:00Z', reason: 'Рабочее время' },
  reengagement_plan: { status: 'scheduled', at: '2026-11-16T09:47:00Z', text: 'Длинный текст '.repeat(100), reason: 'Тема разговора' } },
  messages: [], events: [], client: { id: 1 } } as unknown as DialogDetail;

it('prioritizes pause, starts compact and retains exact plan behind keyboard accessible details', async () => {
  const toggle = vi.fn();
  const { container } = render(<ConversationState detail={detail} onToggleAi={toggle} onResolve={vi.fn()} onReplyNow={vi.fn()} />);
  expect(screen.getAllByRole('status')).toHaveLength(1);
  expect(screen.getByRole('status')).toHaveTextContent('ИИ выключен');
  expect(container.querySelector('details')).not.toHaveAttribute('open');
  const summary = container.querySelector('summary')!;
  summary.focus();
  expect(summary).toHaveFocus();
  await userEvent.click(summary);
  expect(container.querySelector('blockquote')).toBeVisible();
  expect(container.querySelector('blockquote')?.textContent).toBe(detail.dialog.reengagement_plan!.text);
  expect(screen.queryByRole('button', { name: 'Ответить сейчас' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Включить ИИ' }));
  expect(toggle).toHaveBeenCalledWith(false);
});

it('operator requirement alone never claims that AI is paused', () => {
  render(<ConversationState detail={{ ...detail, dialog: { ...detail.dialog, ai_paused: false } }} onToggleAi={vi.fn()} onResolve={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent('Нужен ответ оператора · ИИ включён');
  expect(screen.queryByText('ИИ выключен')).not.toBeInTheDocument();
});
