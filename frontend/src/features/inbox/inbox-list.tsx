import { MessagesSquare, PauseCircle } from 'lucide-react';
import type * as React from 'react';
import { Avatar } from '@/components/avatar';
import { ChannelIcon, NeedsOperatorBadge, StageChip } from '@/components/badges';
import { EmptyState } from '@/components/empty-state';
import { FilterBar, FilterField, SearchInput } from '@/components/filter-bar';
import { OptionSelect } from '@/components/option-select';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Tip } from '@/components/ui/tooltip';
import type { ChannelKind, ClientStatus, DialogFilter, DialogRow, Persona, Stage, Channel } from '@/lib/models';
import { CHANNEL_KINDS, CHANNEL_LABELS, STAGES, STATUSES, STATUS_LABELS } from '@/lib/stages';
import { cn } from '@/lib/utils';
import { DEFAULT_DIALOG_FILTER, LOAD_MORE_THRESHOLD, rowPreview, rowTime } from './inbox-logic';

/** ArrowUp/ArrowDown move focus between dialog rows. */
export function moveRowFocus(e: React.KeyboardEvent<HTMLElement>): void {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button[data-row]'));
  const i = items.indexOf(document.activeElement as HTMLElement);
  const next = items[e.key === 'ArrowDown' ? i + 1 : i - 1];
  if (next) {
    e.preventDefault();
    next.focus();
  }
}

export function InboxFilters({
  filter,
  onChange,
  personas,
  channels,
  activeCount,
}: {
  filter: DialogFilter;
  onChange: (f: DialogFilter) => void;
  personas: Persona[];
  channels: Channel[];
  activeCount: number;
}) {
  const set = <K extends keyof DialogFilter>(key: K, value: DialogFilter[K]): void => onChange({ ...filter, [key]: value });
  return (
    <FilterBar
      alwaysSheet
      activeCount={activeCount}
      onReset={() => onChange(DEFAULT_DIALOG_FILTER)}
      search={<SearchInput value={filter.q} onChange={(q) => set('q', q)} placeholder="Поиск" />}
      filters={
        <>
          <FilterField label="Источник">
            <OptionSelect<DialogFilter["source"]>
              value={filter.source}
              onChange={(v) => set("source", v)}
              options={[{ value: "", label: "Все диалоги" }, { value: "real", label: "Каналы" },
                { value: "test", label: "Тестовые" }]}
            />
          </FilterField>
          <FilterField label="Фото клиента">
            <OptionSelect<DialogFilter["avatar"]>
              value={filter.avatar}
              onChange={(v) => set("avatar", v)}
              options={[{ value: "", label: "Любой" }, { value: "with", label: "С аватаром" },
                { value: "without", label: "Без аватара" }]}
            />
          </FilterField>
          <FilterField label="Аватар / аккаунт">
            <OptionSelect<number | null>
              value={filter.channel_id}
              onChange={(v) => set("channel_id", v)}
              options={[{ value: null, label: "Все аккаунты" }, ...channels.map((ch) => ({
                value: ch.id, label: `${ch.name || ch.account_label || `#${ch.id}`} · ${CHANNEL_LABELS[ch.kind]}`,
              }))]}
            />
          </FilterField>
          <FilterField label="Канал">
            <OptionSelect<ChannelKind | ''>
              value={filter.channel_kind}
              onChange={(v) => set('channel_kind', v)}
              options={[{ value: '', label: 'Все каналы' }, ...CHANNEL_KINDS.map((k) => ({ value: k, label: CHANNEL_LABELS[k] }))]}
            />
          </FilterField>
          <FilterField label="Персонаж">
            <OptionSelect<number | null>
              value={filter.persona_id}
              onChange={(v) => set('persona_id', v)}
              options={[{ value: null, label: 'Все персонажи' }, ...personas.map((p) => ({ value: p.id, label: p.name }))]}
            />
          </FilterField>
          <FilterField label="Этап">
            <OptionSelect<Stage | ''>
              value={filter.stage}
              onChange={(v) => set('stage', v)}
              options={[{ value: '', label: 'Все этапы' }, ...STAGES.map((s) => ({ value: s.id, label: s.label }))]}
            />
          </FilterField>
          <FilterField label="Статус">
            <OptionSelect<ClientStatus | ''>
              value={filter.status}
              onChange={(v) => set('status', v)}
              options={[{ value: '', label: 'Любой статус' }, ...STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))]}
            />
          </FilterField>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="inbox-unread">Только непрочитанные</Label>
            <Switch id="inbox-unread" checked={filter.unread_only} onCheckedChange={(v) => set('unread_only', v)} />
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="inbox-needs-operator">Нужен оператор</Label>
            <Switch id="inbox-needs-operator" checked={filter.needs_operator} onCheckedChange={(v) => set('needs_operator', v)} />
          </div>
        </>
      }
    />
  );
}

export function DialogList({
  rows,
  selectedId,
  loading,
  remaining,
  loadingMore,
  filtered,
  onOpen,
  onLoadMore,
}: {
  rows: DialogRow[];
  selectedId: number | null;
  loading: boolean;
  remaining: number;
  loadingMore: boolean;
  filtered: boolean;
  onOpen: (id: number) => void;
  onLoadMore: () => void;
}) {
  const onScroll = (e: React.UIEvent<HTMLUListElement>): void => {
    const el = e.currentTarget;
    if (remaining > 0 && el.scrollHeight - el.scrollTop - el.clientHeight < LOAD_MORE_THRESHOLD) onLoadMore();
  };
  return (
    <ul role="list" aria-label="Диалоги" className="min-h-0 flex-1 overflow-y-auto scrollbar-thin" onKeyDown={moveRowFocus} onScroll={onScroll}>
      {loading && rows.length === 0 &&
        Array.from({ length: 6 }, (_, i) => (
          <li key={i} className="flex gap-3 px-3 py-3">
            <Skeleton className="size-10 rounded-full" />
            <div className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-3 w-3/4" />
            </div>
          </li>
        ))}
      {!loading && rows.length === 0 && (
        <li>
          <EmptyState
            icon={MessagesSquare}
            title="Диалогов нет"
            hint={filtered ? 'Попробуйте сбросить фильтры.' : 'Они появятся, когда клиенты напишут в каналы.'}
          />
        </li>
      )}
      {rows.map((row) => {
        const selected = row.id === selectedId;
        return (
          <li key={row.id}>
            <button
              type="button"
              data-row
              aria-current={selected ? 'true' : undefined}
              onClick={() => onOpen(row.id)}
              className={cn(
                'flex w-full gap-3 border-b px-3 py-3 text-left transition-colors outline-none hover:bg-muted/60 focus-visible:bg-muted',
                selected && 'bg-accent hover:bg-accent',
              )}
            >
              <Avatar name={row.client.display_name} url={row.client.avatar_url} size={40} />
              <span className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="flex items-baseline justify-between gap-2">
                  <strong className="truncate text-sm font-medium">{row.client.display_name}</strong>
                  <span className="flex-none text-xs text-muted-foreground tabular">{rowTime(row)}</span>
                </span>
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm text-muted-foreground">{rowPreview(row)}</span>
                  {row.unread > 0 && (
                    <span
                      aria-label={`${row.unread} непрочитанных`}
                      className="flex h-5 min-w-5 flex-none items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold text-primary-foreground tabular"
                    >
                      {row.unread}
                    </span>
                  )}
                </span>
                <span className="flex flex-wrap items-center gap-1.5">
                  {row.is_test ? <span className="rounded bg-warning/15 px-1.5 text-[11px] font-semibold">Тест</span>
                    : <ChannelIcon kind={row.channel_kind} size={18} />}
                  <StageChip stage={row.client.stage} className="h-5 px-2 text-[11px]" />
                  <span className="truncate text-xs text-muted-foreground">{row.persona.name}</span>
                  {row.ai_paused && (
                    <Tip label="ИИ на паузе">
                      <PauseCircle aria-label="ИИ на паузе" className="size-4 text-warning" />
                    </Tip>
                  )}
                  {row.needs_operator && <NeedsOperatorBadge />}
                </span>
              </span>
            </button>
          </li>
        );
      })}
      {remaining > 0 && (
        <li className="flex justify-center p-3">
          <Button variant="ghost" size="sm" disabled={loadingMore} onClick={onLoadMore}>
            {loadingMore ? 'Загрузка…' : `Показать ещё (${remaining})`}
          </Button>
        </li>
      )}
    </ul>
  );
}
