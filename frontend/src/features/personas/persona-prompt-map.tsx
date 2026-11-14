import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import { ErrorState } from '@/components/empty-state';
import { FixedPrompt } from '@/components/fixed-prompt';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { personasApi } from '@/lib/api/personas';
import type { PromptPart } from '@/lib/models';
import { qk } from '@/lib/query';
import { editTarget, personaFieldAnchor, rulePlace, settingAnchor, sourceLabel } from './persona-prompt-logic';

const LINK = 'text-xs font-medium text-primary underline-offset-4 hover:underline';
const TEXT = 'max-h-72 overflow-auto rounded-lg bg-muted/60 p-3 text-[12px] whitespace-pre-wrap [overflow-wrap:anywhere]';

function EditLink({ part, strategyId }: { part: PromptPart; strategyId: number }) {
  const target = editTarget(part, strategyId);
  if (target === null) return null;
  return target.kind === 'anchor' ? (
    <a href={`#${target.anchor}`} className={LINK}>Изменить</a>
  ) : (
    <Link to="/strategies/$id" params={{ id: String(target.strategyId) }} className={LINK}>Изменить в стратегии</Link>
  );
}

function Piece({ title, badge, action, text }: { title: string; badge: string; action: ReactNode; text: string }) {
  return (
    <li className="flex flex-col gap-1.5" aria-label={title}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h3 className="text-sm font-medium">{title}</h3>
        <Badge variant="muted">{badge}</Badge>
        <span className="flex-1" />
        {action}
      </div>
      <pre className={TEXT}>{text}</pre>
    </li>
  );
}

/**
 * The assembled prompt of the saved persona, piece by piece in the order the model gets it: whose
 * text each piece is and where to change it; then the per-reply context code builds, and the
 * rules fixed in the code. Loads next to the page, never blocks it.
 */
export function PersonaPromptMap({ personaId }: { personaId: number }) {
  const [whole, setWhole] = useState(false);
  const query = useQuery({ queryKey: qk.personaPrompt(personaId), queryFn: () => personasApi.prompt(personaId) });

  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError) return <ErrorState onRetry={() => void query.refetch()} />;
  const map = query.data;

  return (
    <div className="flex flex-col gap-4 text-sm">
      <p className="text-xs text-muted-foreground">
        По сохранённым данным. Модель получает системное сообщение из частей ниже, затем историю диалога, затем
        контекст разговора. После ответа черновик проверяет{' '}
        <a href={`#${settingAnchor('guard_prompt')}`} className={LINK}>охранник</a>, а опечатки и регистр добавляет код по
        настройкам <a href={`#${settingAnchor('typo_percent')}`} className={LINK}>«Стиль сообщений»</a>.
      </p>
      <ol className="flex flex-col gap-4" aria-label="Части промпта">
        {map.parts.map((part) => (
          <Piece
            key={part.key}
            title={part.title}
            badge={sourceLabel(part.source)}
            action={<EditLink part={part} strategyId={map.strategy_id} />}
            text={part.text}
          />
        ))}
        <Piece
          title="Контекст разговора"
          badge="Собирает код на каждый ответ"
          text={map.context}
          action={
            <span className="flex flex-wrap gap-2">
              <a href={`#${personaFieldAnchor('birth_date')}`} className={LINK}>Дата рождения</a>
              <a href={`#${personaFieldAnchor('timezone')}`} className={LINK}>Часовой пояс</a>
              <a href={`#${personaFieldAnchor('routine')}`} className={LINK}>Распорядок</a>
            </span>
          }
        />
      </ol>
      <p className="text-xs text-muted-foreground">
        Контекст показан на эту минуту: возраст и знак считаются из даты рождения, местное время из часового пояса,
        «где ты сейчас» из распорядка. Данные клиента в примере условные.
      </p>
      <Button
        variant="ghost"
        size="sm"
        className="self-start px-0 text-muted-foreground"
        aria-expanded={whole}
        onClick={() => setWhole((v) => !v)}
      >
        {whole ? 'Скрыть системное сообщение одним текстом' : 'Показать системное сообщение одним текстом'}
      </Button>
      {whole && <pre aria-label="Полный промпт" className={TEXT}>{map.system}</pre>}
      <div className="flex flex-col gap-3" role="group" aria-label="Фиксированные правила">
        <h3 className="text-sm font-medium">Фиксированные правила</h3>
        {map.rules.map((rule) => (
          <div key={rule.key} className="flex flex-col gap-1.5">
            <span className="text-xs font-medium">{rule.title}</span>
            <FixedPrompt text={rule.text} where={rulePlace(rule.key)} />
          </div>
        ))}
      </div>
    </div>
  );
}
