import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { dialogsApi } from '@/lib/api/dialogs';
import { formatDateTime } from '@/lib/format';
import type { DialogSummary } from '@/lib/models';
import { qk } from '@/lib/query';
import { messagesWord } from './inbox-logic';

/** What the summary covers, in one line; '' while there is no summary. */
export function summaryCoverage(summary: DialogSummary): string {
  if (!summary.covered_until) return '';
  return `Охватывает переписку до ${formatDateTime(summary.covered_until)} · ` +
    `${summary.messages_covered} ${messagesWord(summary.messages_covered)}`;
}

/**
 * The dialog's summary of the messages older than the context window: read only, with its
 * coverage date. «Обновить» asks the server to fold in the messages that have left the window
 * since (the summary is extended, never rebuilt).
 */
export function DialogSummaryBlock({ dialogId }: { dialogId: number }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: qk.dialogSummary(dialogId), queryFn: () => dialogsApi.summary(dialogId) });
  const refresh = useMutation({
    mutationFn: () => dialogsApi.refreshSummary(dialogId),
    onSuccess: (data) => queryClient.setQueryData(qk.dialogSummary(dialogId), data),
  });
  const summary = query.data;
  return (
    <section aria-label="Саммари диалога" className="flex flex-col gap-1.5 text-sm">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs text-muted-foreground">Саммари диалога</h3>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={!summary || refresh.isPending}
          onClick={() => refresh.mutate()}>
          <RefreshCw className={refresh.isPending ? 'animate-spin' : undefined} />
          Обновить
        </Button>
      </div>
      {query.isError ? (
        <p className="text-xs text-destructive">Не удалось загрузить саммари.</p>
      ) : !summary ? (
        <p className="text-xs text-muted-foreground">Загружаем…</p>
      ) : summary.text ? (
        <>
          <p className="whitespace-pre-wrap">{summary.text}</p>
          <p className="text-xs text-muted-foreground">
            {summaryCoverage(summary)}
            {summary.updated_at ? ` · обновлено ${formatDateTime(summary.updated_at)}` : ''}
          </p>
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          Пока пусто: персонаж читает последние сообщения целиком, саммари появится, когда часть переписки станет старше окна контекста.
        </p>
      )}
      {summary && summary.pending > 0 && (
        <p className="text-xs text-muted-foreground">
          Ещё не вошло в саммари: {summary.pending} {messagesWord(summary.pending)}.
        </p>
      )}
      {refresh.isError && <p role="alert" className="text-xs text-destructive">Не удалось обновить саммари.</p>}
    </section>
  );
}
