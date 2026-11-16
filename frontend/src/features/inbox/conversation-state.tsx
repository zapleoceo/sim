import { Button } from '@/components/ui/button';
import { formatZonedDateTime } from '@/lib/format';
import type { DialogDetail } from '@/lib/models';
import { pauseNotice, replyPlanNotice } from './inbox-logic';

const PLAN_LABELS: Record<string, string> = { analyzing: 'Анализируется', scheduled: 'Запланировано',
  outdated: 'План устарел', no_send: 'Писать не нужно', blocked: 'Не прошло проверку',
  failed: 'Ошибка планирования', sent: 'Отправлено', cancelled: 'Отменено' };

/** One current state; historical reasons stay in the feed rather than competing banners. */
export function ConversationState({ detail, onToggleAi, onReplyNow, onResolve }: {
  detail: DialogDetail; onToggleAi: (paused: boolean) => void;
  onReplyNow?: () => void; onResolve: () => void;
}) {
  const d = detail.dialog;
  const paused = pauseNotice(d);
  const delayed = replyPlanNotice(d);
  const plan = d.reengagement_plan;
  const title = d.ai_paused ? 'ИИ выключен' : d.needs_operator ? 'Нужен ответ оператора · ИИ включён'
    : delayed ? 'Ответ отложен' : plan ? `Следующее сообщение: ${PLAN_LABELS[plan.status] ?? plan.status}` : 'ИИ включён';
  const handoff = detail.events?.filter(e => e.kind === 'handoff').at(-1);
  return <section aria-label="Состояние чата" className="flex-none border-b bg-background px-3 py-2 sm:px-4">
    <details className="group">
      <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-primary">
        <span role="status" aria-label={title}>{title}</span><span className="ml-2 text-xs font-normal text-muted-foreground">Подробности</span>
      </summary>
      <div className="mt-2 max-h-[35dvh] space-y-3 overflow-y-auto text-sm [overflow-wrap:anywhere]">
        {paused && <div><p>{paused.text}</p><Button size="sm" onClick={() => onToggleAi(false)}>Включить ИИ</Button></div>}
        {d.needs_operator && <div><p>Проверьте переписку и ответьте, если это необходимо.</p>
          {handoff?.reason && <p className="text-xs text-muted-foreground">Причина: {handoff.reason}</p>}
          <Button variant="outline" size="sm" onClick={onResolve}>Обработано</Button></div>}
        {d.reply_plan && <div aria-label="Отложенный ответ"><p>{d.ai_paused ? 'Отправка приостановлена. ' : ''}
          {formatZonedDateTime(d.reply_plan.at)} (ваше время)</p><p className="text-xs text-muted-foreground">{d.reply_plan.reason}</p>
          {!d.ai_paused && onReplyNow && <Button size="sm" variant="outline" onClick={onReplyNow}>Ответить сейчас</Button>}</div>}
        {plan && <div aria-label="План продолжения разговора" className="rounded-lg border bg-muted/30 p-3">
          <strong>{PLAN_LABELS[plan.status] ?? plan.status}</strong>
          {d.ai_paused && <p>ИИ выключен: этот план не означает, что сообщение будет отправлено.</p>}
          {plan.at && <p className="text-xs">{formatZonedDateTime(plan.at)} (ваше время)</p>}
          {plan.text && <blockquote className="my-2 whitespace-pre-wrap border-l-2 pl-3">{plan.text}</blockquote>}
          {plan.reason && <p className="text-xs text-muted-foreground">{plan.reason}</p>}
        </div>}
        {!paused && !d.needs_operator && !d.reply_plan && !plan && <p className="text-xs text-muted-foreground">Нового плана пока нет. Отправка зависит от контекста и доступности канала.</p>}
      </div>
    </details>
  </section>;
}
