import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { DialogDetail, ReplyPlan } from '@/lib/models';
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


it('distinguishes fresh writing, uncertain recovery, queued work, and completion after refresh', async () => {
  const onReplyNow = vi.fn();
  const plan = (phase: NonNullable<ReplyPlan['phase']>, writing: boolean): ReplyPlan => ({
    at: '2026-11-16T09:47:00Z', reason: 'Synthetic detail', cls: '', urgent: null, phase, writing,
  });
  const view = (reply_plan: ReplyPlan | null) => <ConversationState
    detail={{ ...detail, dialog: { ...detail.dialog, ai_paused: false, needs_operator: false,
      reengagement_plan: null, reply_plan } }}
    onToggleAi={vi.fn()} onResolve={vi.fn()} onReplyNow={onReplyNow}
  />;
  const { container, rerender } = render(view(plan('writing', true)));
  expect(screen.getByRole('status')).toHaveTextContent('Пишет ответ');
  await userEvent.click(container.querySelector('summary')!);

  rerender(view(plan('running_or_recovering', true)));
  expect(screen.getByRole('status')).toHaveTextContent('Ответ готовится или ожидает восстановления');
  expect(screen.queryByText('Пишет ответ')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Ответить сейчас' })).not.toBeInTheDocument();

  rerender(view(plan('queued', false)));
  expect(screen.getByRole('status')).toHaveTextContent('Ответ запланирован');
  expect(screen.getByRole('button', { name: 'Ответить сейчас' })).toBeVisible();
  expect(onReplyNow).not.toHaveBeenCalled();

  rerender(view(null));
  expect(screen.getByRole('status')).toHaveTextContent('ИИ включён');
});
