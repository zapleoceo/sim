import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import { ArrowLeft, Hourglass, MessagesSquare } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { Avatar } from '@/components/avatar';
import { StageChip, StatusBadge } from '@/components/badges';
import { useConfirm } from '@/components/confirm';
import { EmptyState } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { OptionSelect } from '@/components/option-select';
import { SINGLE_PANE_QUERY, WIDE_QUERY, useIsPhone, useMediaQuery } from '@/hooks/use-media-query';
import { useDebounced } from '@/hooks/use-debounced';
import { dialogsApi } from '@/lib/api/dialogs';
import { channelsApi } from '@/lib/api/channels';
import { clientsApi } from '@/lib/api/clients';
import { simulationsApi } from '@/lib/api/simulations';
import { personasApi } from '@/lib/api/personas';
import type { Dialog, DialogDetail, DialogFilter, DialogRow, Message, Persona, SimBranchDetail, SimBranchRow, Stage } from '@/lib/models';
import { useAuth } from '@/lib/auth';
import { useLiveDialogs } from '@/lib/live';
import { qk } from '@/lib/query';
import { cn } from '@/lib/utils';
import { Conversation } from './conversation';
import { DialogList, InboxFilters } from './inbox-list';
import {
  CLEAR_CHAT_CONFIRM, DEFAULT_DIALOG_FILTER, PAGE_SIZE, POLL_MS, activeFilterCount, appendPage, clearedRow, combineDialogRows,
  mergeFirstPage, normalizeFilter, visibleTestRows,
} from './inbox-logic';

const route = getRouteApi('/_app/inbox');

function ClientSideCard({ detail, personas }: { detail: DialogDetail; personas: Persona[] }) {
  const c = detail.client;
  const personaName = personas.find((p) => p.id === detail.dialog.persona_id)?.name ?? '';
  return (
    <aside aria-label="Карточка клиента" className="flex w-80 flex-none flex-col gap-4 overflow-y-auto border-l p-4 scrollbar-thin">
      <div className="flex flex-col items-center gap-2 text-center">
        <Avatar name={c.display_name} url={c.avatar_url} size={56} />
        <strong>{c.display_name}</strong>
        <span className="flex flex-wrap justify-center gap-1.5">
          <StageChip stage={c.stage} />
          <StatusBadge status={c.status} />
        </span>
      </div>
      {personaName && <SideField label="Персонаж">{personaName}</SideField>}
      {c.next_step && <SideField label="Следующий шаг">{c.next_step}</SideField>}
      {c.needs.length > 0 && (
        <SideField label="Потребности">
          <span className="flex flex-wrap gap-1">
            {c.needs.map((n) => (
              <span key={n} className="rounded-md bg-muted px-2 py-0.5 text-xs">
                {n}
              </span>
            ))}
          </span>
        </SideField>
      )}
      {c.notes && (
        <SideField label="Заметки">
          <span className="whitespace-pre-wrap">{c.notes}</span>
        </SideField>
      )}
      <Button asChild variant="outline">
        <Link to="/clients/$id" params={{ id: String(c.id) }}>
          Открыть карточку
        </Link>
      </Button>
    </aside>
  );
}

function TestConversation({ branch, onBack }: { branch: SimBranchDetail; onBack?: () => void }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<{ text: string; id: string } | null>(null);
  const canSend = branch.can_continue === true && !branch.pending;
  const send = async (): Promise<void> => {
    const value = text.trim();
    if (!value || !canSend || sending) return;
    const id = request.current?.text === value ? request.current.id : crypto.randomUUID();
    request.current = { text: value, id };
    setSending(true);
    setError('');
    try {
      await simulationsApi.send(branch.id, value, id);
      setText('');
      request.current = null;
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['inbox-test-dialog', -branch.id] }),
        queryClient.invalidateQueries({ queryKey: ['inbox-simulations'] }),
      ]);
    } catch {
      setError('Не удалось подтвердить тестовое сообщение. Повтор использует тот же запрос.');
    } finally {
      setSending(false);
    }
  };
  return (
    <section aria-label="Тестовый диалог" className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-2 border-b px-2 py-2 sm:p-4">
        {onBack && (
          <Button variant="ghost" size="icon-sm" aria-label="Назад к списку" onClick={onBack}>
            <ArrowLeft />
          </Button>
        )}
        <div className="min-w-0">
          <strong className="block truncate">{branch.title || 'Тестовый диалог'}</strong>
          <p className="truncate text-xs text-muted-foreground">Тест · {branch.persona.name} · этап {branch.stage}</p>
        </div>
      </header>
      <div role="log" aria-label="Сообщения тестового диалога" className="min-h-0 flex-1 overflow-y-auto p-2 sm:p-4">
        {branch.messages.map((message) => (
          <div key={message.id} className="mb-3 rounded-lg border p-3">
            <p className="text-xs text-muted-foreground">
              {message.role === 'client' ? 'Тестовый клиент' : message.role === 'system' ? 'Система' : 'Персонаж'}
              {' · '}{new Date(message.created_at).toLocaleString()}
            </p>
            <p className="whitespace-pre-wrap">{message.text}</p>
            {message.guard?.verdict === 'blocked' && <p className="text-xs text-destructive">Не отправлено: контролёр отклонил</p>}
          </div>
        ))}
        {branch.pending && <p role="status" className="text-sm">Персонаж готовит ответ…</p>}
      </div>
      <form className="flex flex-none flex-col gap-2 border-t p-2 sm:p-4" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <p className="text-xs text-muted-foreground">Это симуляция: сообщения остаются здесь и не уходят в канал клиента.</p>
        {!branch.can_continue && <p className="text-xs text-warning">
          {branch.closed_at || branch.status === 'refused' || branch.status === 'paused'
            ? 'Ветка завершена или передана оператору. Начните новый тест.'
            : 'Продолжать тест может только его автор.'}
        </p>}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        <textarea aria-label="Сообщение тестовому клиенту" value={text}
          onChange={(event) => { setText(event.target.value); request.current = null; }}
          disabled={!branch.can_continue || sending} rows={2} maxLength={4000}
          className="w-full rounded-md border bg-background p-2 text-[16px] md:text-sm" />
        <Button type="submit" disabled={!canSend || sending || !text.trim()}>
          {sending ? 'Отправка…' : 'Продолжить тест'}
        </Button>
      </form>
    </section>
  );
}

function SideField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

export function InboxPage() {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const queryClient = useQueryClient();
  const { isOwner } = useAuth();
  const confirm = useConfirm();
  const dialogId = search.d ?? null;
  useLiveDialogs();   // the server pushes changed dialog ids: list and open dialog refresh at once

  const narrow = useMediaQuery(SINGLE_PANE_QUERY);
  const wide = useMediaQuery(WIDE_QUERY);
  const phone = useIsPhone();
  const [cardOpen, setCardOpen] = useState(true);
  const [newTestPersonaId, setNewTestPersonaId] = useState<number | null>(null);

  const [filter, setFilter] = useState<DialogFilter>(DEFAULT_DIALOG_FILTER);
  const debouncedQ = useDebounced(filter.q, 250);
  const apiFilter = useMemo(() => normalizeFilter({ ...filter, q: debouncedQ }), [filter, debouncedQ]);
  const filterKey = JSON.stringify(apiFilter);

  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const channels = useQuery({ queryKey: ['inbox-channels'], queryFn: channelsApi.list });
  const operatorQueue = useQuery({
    queryKey: ['inbox-operator-queue'],
    queryFn: () => dialogsApi.list({ ...DEFAULT_DIALOG_FILTER, needs_operator: true },
      { limit: 1, offset: 0 }, true),
    refetchInterval: POLL_MS,
  });
  const startTest = useMutation({
    mutationFn: (personaId: number) => simulationsApi.start(personaId),
    onSuccess: (branch) => {
      void queryClient.invalidateQueries({ queryKey: ['inbox-simulations'] });
      void navigate({ search: (current) => ({ ...current, d: -branch.id }) });
    },
  });
  const simulations = useQuery({
    queryKey: ["inbox-simulations"],
    queryFn: async () => {
      const items: SimBranchRow[] = [];
      for (let offset = 0; ; offset += 200) {
        const page = await simulationsApi.list(null, { limit: 200, offset });
        items.push(...page.items);
        if ((page.total !== undefined && items.length >= page.total) || page.items.length === 0) break;
      }
      return items;
    },
    retry: false,
    refetchInterval: 30_000,
  });

  // List: first page refetched every 5 s; the loaded tail is kept between polls.
  const [rows, setRows] = useState<DialogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const rowsFor = useRef<string | null>(null);
  const list = useQuery({
    queryKey: qk.dialogs(apiFilter),
    queryFn: () => dialogsApi.list(apiFilter, { limit: PAGE_SIZE, offset: 0 }, true),
    refetchInterval: POLL_MS,
  });
  useEffect(() => {
    if (!list.data) return;
    const reset = rowsFor.current !== filterKey;
    rowsFor.current = filterKey;
    setRows((prev) => mergeFirstPage(prev, list.data.items, reset));
    setTotal(list.data.total ?? list.data.items.length);
  }, [list.data, filterKey]);

  const testRows = visibleTestRows(simulations.data ?? [], personas.data ?? [], filter);
  const visibleRows = combineDialogRows(filter.source === "test" ? [] : rows,
    filter.source === "real" ? [] : testRows);
  const remaining = filter.source === "test" ? 0 : Math.max(0, total - rows.length);

  const loadMore = (): void => {
    if (loadingMore || remaining === 0) return;
    setLoadingMore(true);
    dialogsApi
      .list(apiFilter, { limit: PAGE_SIZE, offset: rows.length }, true)
      .then((res) => {
        setRows((prev) => appendPage(prev, res.items));
        if (res.total !== undefined) setTotal(res.total);
      })
      .catch(() => undefined)
      .finally(() => setLoadingMore(false));
  };

  // Open dialog: loaded and polled every 5 s (silent after the first load).
  const loadedOnce = useRef<number | null>(null);
  const detailQuery = useQuery({
    queryKey: qk.dialog(dialogId ?? 0),
    queryFn: async () => {
      const id = dialogId as number;
      const d = await dialogsApi.get(id, loadedOnce.current === id);
      loadedOnce.current = id;
      return d;
    },
    enabled: dialogId !== null && dialogId > 0,
    refetchInterval: POLL_MS,
  });
  const detail = dialogId !== null && dialogId > 0 && detailQuery.data?.dialog.id === dialogId ? detailQuery.data : null;
  const testDetail = useQuery({
    queryKey: ["inbox-test-dialog", dialogId],
    queryFn: () => simulationsApi.get(-(dialogId as number)),
    enabled: dialogId !== null && dialogId < 0,
    refetchInterval: POLL_MS,
  });

  // Mark read when the open dialog has unread messages.
  useEffect(() => {
    if (!detail) return;
    const id = detail.dialog.id;
    const row = rows.find((r) => r.id === id);
    if (row && row.unread > 0) {
      setRows((prev) => prev.map((r) => (r.id === id ? { ...r, unread: 0 } : r)));
      dialogsApi.markRead(id).catch(() => undefined);
    }
  }, [detail, rows]);

  const applyFlags = (id: number, flags: Pick<Dialog, 'ai_paused' | 'needs_operator'>): void => {
    queryClient.setQueryData<DialogDetail>(qk.dialog(id), (d) => (d ? { ...d, dialog: { ...d.dialog, ...flags } } : d));
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...flags } : r)));
  };

  const acceptMessage = (id: number, msg: Message): void => {
    queryClient.setQueryData<DialogDetail>(qk.dialog(id), (d) =>
      d ? { ...d, dialog: { ...d.dialog, ai_paused: true, needs_operator: false },
        messages: d.messages.some((m) => m.id === msg.id) ? d.messages : [...d.messages, msg] } : d,
    );
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ai_paused: true, needs_operator: false } : r)));
  };

  const sendMutation = useMutation({
    mutationFn: ({ id, text, requestId }: { id: number; text: string; requestId: string }) =>
      dialogsApi.send(id, text, requestId),
    onSuccess: (msg, { id }) => acceptMessage(id, msg),
  });
  const mediaMutation = useMutation({
    mutationFn: (item: { id: number; kind: 'voice'; file: Blob; filename: string; requestId: string } |
      { id: number; kind: 'file'; file: File; caption: string; requestId: string }) =>
      item.kind === 'voice'
        ? dialogsApi.sendVoice(item.id, item.file, item.filename, item.requestId)
        : dialogsApi.sendAttachment(item.id, item.file, item.caption, item.requestId),
    onSuccess: (msg, item) => acceptMessage(item.id, msg),
  });

  const changeStage = async (clientId: number, stage: Stage): Promise<boolean> => {
    try {
      const updated = await clientsApi.changeStage(clientId, { stage, reason: 'Из чата оператором' });
      if (dialogId !== null) queryClient.setQueryData<DialogDetail>(qk.dialog(dialogId), (d) =>
        d ? { ...d, client: updated } : d);
      setRows((prev) => prev.map((r) => r.client.id === clientId ?
        { ...r, client: { ...r.client, stage: updated.stage } } : r));
      return true;
    } catch { return false; }
  };

  // «Очистить чат» (owner only): the server answers with the emptied dialog, shown at once.
  const clearChat = useMutation({
    mutationFn: (id: number) => dialogsApi.clearChat(id),
    onSuccess: (cleared, id) => {
      queryClient.setQueryData<DialogDetail>(qk.dialog(id), cleared);
      setRows((prev) => prev.map((r) => (r.id === id ? clearedRow(r) : r)));
      void queryClient.invalidateQueries({ queryKey: ['dialogs'] });
      void queryClient.invalidateQueries({ queryKey: ['client-chat'] });
      toast.success('Чат очищен');
    },
  });
  const askClearChat = async (): Promise<void> => {
    if (dialogId === null) return;
    const id = dialogId;
    if (await confirm(CLEAR_CHAT_CONFIRM)) clearChat.mutate(id);
  };

  const toggleAi = (paused: boolean): void => {
    if (dialogId === null) return;
    const id = dialogId;
    dialogsApi.setAiPaused(id, paused).then((d) => applyFlags(id, d)).catch(() => undefined);
  };

  const resolve = (): void => {
    if (dialogId === null) return;
    const id = dialogId;
    dialogsApi.resolve(id).then((d) => applyFlags(id, d)).catch(() => undefined);
  };

  const open = (id: number): void => void navigate({ search: (s) => ({ ...s, d: id }) });
  const back = (): void => void navigate({ search: (s) => ({ ...s, d: undefined }) });

  const showList = !narrow || dialogId === null;
  const showConversation = !narrow || dialogId !== null;
  const showCard = wide && cardOpen && detail !== null;
  const count = activeFilterCount(filter);

  return (
    <div data-fit-viewport className="flex h-[calc(var(--app-vh,100dvh)-var(--bottom-nav))] min-h-0 md:h-dvh">
      {showList && (
        <section
          aria-label="Список диалогов"
          className={cn('flex min-h-0 flex-col border-r', narrow ? 'w-full' : 'w-[340px] flex-none xl:w-[380px]')}
        >
          <div className="flex-none border-b p-2 sm:p-3">
            <InboxFilters filter={filter} onChange={setFilter} personas={personas.data ?? []}
              channels={channels.data ?? []} activeCount={count} />
          </div>
          {(operatorQueue.data?.total ?? 0) > 0 && <button type="button"
            className="w-full border-b bg-warning/15 px-3 py-1.5 text-left text-xs font-semibold sm:py-2 sm:text-sm"
            onClick={() => setFilter({ ...DEFAULT_DIALOG_FILTER, source: 'real', needs_operator: true })}>
            Требуется ответ менеджера: {operatorQueue.data?.total} · открыть очередь
          </button>}
          <div className="flex items-center gap-2 border-b p-2">
            <OptionSelect<number | null> value={newTestPersonaId} onChange={setNewTestPersonaId}
              options={[{ value: null, label: 'Персонаж для теста' },
                ...(personas.data ?? []).filter((p) => p.is_active).map((p) => ({ value: p.id, label: p.name }))]} />
            <Button type="button" size="sm" disabled={!newTestPersonaId || startTest.isPending}
              onClick={() => { if (newTestPersonaId) startTest.mutate(newTestPersonaId); }}>
              Новый тест
            </Button>
          </div>
          {startTest.isError && <p role="alert" className="p-2 text-xs text-destructive">Не удалось создать тест.</p>}
          <DialogList
            rows={visibleRows}
            selectedId={dialogId}
            loading={list.isPending || simulations.isPending}
            remaining={remaining}
            loadingMore={loadingMore}
            filtered={count > 0}
            onOpen={open}
            onLoadMore={loadMore}
          />
        </section>
      )}

      {showConversation && (
        <section aria-label="Диалог" className="flex min-h-0 min-w-0 flex-1 flex-col">
          {dialogId !== null && dialogId < 0 && testDetail.data ? (
            <TestConversation branch={testDetail.data} onBack={narrow ? back : undefined} />
          ) : detail ? (
            <Conversation
              key={detail.dialog.id}
              detail={detail}
              narrow={narrow}
              compact={phone}
              wide={wide}
              cardOpen={cardOpen}
              sending={sendMutation.isPending || mediaMutation.isPending}
              onBack={back}
              onToggleCard={() => setCardOpen((v) => !v)}
              onToggleAi={toggleAi}
              onResolve={resolve}
              onSend={(text, requestId) =>
                sendMutation.mutateAsync({ id: detail.dialog.id, text, requestId })
                  .then(() => true).catch(() => false)
              }
              onSendVoice={(file, filename, requestId) =>
                mediaMutation.mutateAsync({ id: detail.dialog.id, kind: "voice", file, filename, requestId })
                  .then(() => true).catch(() => false)
              }
              onSendAttachment={(file, caption, requestId) =>
                mediaMutation.mutateAsync({ id: detail.dialog.id, kind: "file", file, caption, requestId })
                  .then(() => true).catch(() => false)
              }
              onChangeStage={(stage) => changeStage(detail.client.id, stage)}
              onClearChat={isOwner ? () => void askClearChat() : undefined}
              clearing={clearChat.isPending}
            />
          ) : dialogId !== null ? (
            <div className="grid flex-1 place-items-center">
              {detailQuery.isError || testDetail.isError ? (
                <EmptyState icon={MessagesSquare} title="Диалог не открылся" hint="Возможно, он удалён.">
                  <Button variant="outline" onClick={back}>
                    К списку
                  </Button>
                </EmptyState>
              ) : (
                <EmptyState icon={Hourglass} title="Загружаем диалог…" />
              )}
            </div>
          ) : (
            <div className="grid flex-1 place-items-center">
              <EmptyState icon={MessagesSquare} title="Выберите диалог" hint="Слева — единый список WhatsApp, Telegram и Instagram." />
            </div>
          )}
        </section>
      )}

      {showCard && detail && <ClientSideCard detail={detail} personas={personas.data ?? []} />}
    </div>
  );
}
