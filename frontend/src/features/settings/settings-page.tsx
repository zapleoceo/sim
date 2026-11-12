import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { ErrorState } from '@/components/empty-state';
import { Field } from '@/components/field';
import { modelOptions } from '@/components/model-options';
import { OptionSelect } from '@/components/option-select';
import { Page, PageHeader } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { personasApi } from '@/lib/api/personas';
import { settingsApi } from '@/lib/api/settings';
import { formatDateTime } from '@/lib/format';
import type { AppSettingItem } from '@/lib/models';
import { qk } from '@/lib/query';
import { groupSettings, parseSettingValue, pickSettings, replaceSetting, settingHint } from './settings-logic';

const MODEL_AUTO = 'По умолчанию — маршрут Broker';

/** A Broker model picker; falls back to a plain input when Broker does not answer. */
function ModelInput({ value, onChange, ...control }: {
  value: string;
  onChange: (v: string) => void;
  id?: string;
  'aria-invalid'?: boolean;
  'aria-describedby'?: string;
}) {
  const models = useQuery({ queryKey: qk.models, queryFn: personasApi.models, retry: false });
  if (models.isError) return <Input {...control} value={value} onChange={(e) => onChange(e.target.value)} />;
  return (
    <OptionSelect
      {...control}
      value={value}
      onChange={onChange}
      options={modelOptions(models.data ?? [], value, MODEL_AUTO)}
      disabled={models.isPending}
    />
  );
}

/** One setting: always open, own draft, «Сохранить» and «Вернуть по умолчанию». */
function SettingField({ item }: { item: AppSettingItem }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(String(item.value));
  const [error, setError] = useState('');
  const dirty = draft !== String(item.value);

  const apply = (updated: AppSettingItem, message: string): void => {
    queryClient.setQueryData<AppSettingItem[]>(qk.settings, (list) => (list ? replaceSetting(list, updated) : list));
    setDraft(String(updated.value));
    toast.success(message);
  };
  const save = useMutation({
    mutationFn: (value: string | number) => settingsApi.save(item.key, value),
    onSuccess: (updated) => apply(updated, 'Сохранено'),
  });
  const reset = useMutation({
    mutationFn: () => settingsApi.reset(item.key),
    onSuccess: (updated) => apply(updated, 'Возвращено значение по умолчанию'),
  });
  const busy = save.isPending || reset.isPending;

  const onSave = (): void => {
    const parsed = parseSettingValue(item, draft);
    if (!parsed.ok) {
      setError(parsed.error);
      return;
    }
    setError('');
    save.mutate(parsed.value);
  };

  return (
    <div className="flex flex-col gap-2">
      <Field label={item.label} hint={settingHint(item)} error={error}>
        {item.kind === 'model' ? (
          <ModelInput value={draft} onChange={setDraft} />
        ) : item.kind === 'text' ? (
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-24 max-h-[70vh] font-mono text-[13px] md:text-[13px]"
          />
        ) : (
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            inputMode={item.kind === 'int' ? 'numeric' : undefined}
            className={item.kind === 'int' ? 'max-w-40 tabular' : undefined}
          />
        )}
      </Field>
      <div className="flex flex-wrap items-center gap-2">
        {item.is_default ? (
          <Badge variant="muted">по умолчанию</Badge>
        ) : (
          <Badge variant="warning">изменено{item.updated_at ? ` · ${formatDateTime(item.updated_at)}` : ''}</Badge>
        )}
        {dirty && <span className="text-xs text-muted-foreground">есть несохранённые правки</span>}
        <span className="flex-1" />
        {!item.is_default && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => reset.mutate()}>
            <RotateCcw />
            Вернуть по умолчанию
          </Button>
        )}
        <Button size="sm" disabled={busy || !dirty} onClick={onSave}>
          Сохранить
        </Button>
      </div>
    </div>
  );
}

/** Settings of one or more groups on one page; every field is open, no hidden sections. */
function SettingsView({ title, subtitle, only, except, keys }: {
  title: string;
  subtitle: string;
  /** Show only these groups (by name). */
  only?: readonly string[];
  /** Hide these groups (by name). */
  except?: readonly string[];
  /** Show only these settings, in this order (other keys of the group stay in the DB). */
  keys?: readonly string[];
}) {
  const query = useQuery({ queryKey: qk.settings, queryFn: settingsApi.list });
  const groups = groupSettings(pickSettings(query.data ?? [], keys)).filter(
    (g) => (!only || only.includes(g.name)) && !(except ?? []).includes(g.name),
  );

  return (
    <Page width="narrow">
      <PageHeader title={title} subtitle={subtitle} />
      {query.isPending && (
        <div className="flex flex-col gap-4" aria-busy="true">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      )}
      {query.isError && <ErrorState onRetry={() => void query.refetch()} />}
      {groups.map((g) => (
        <section key={g.name} aria-label={g.name}>
          <Card>
          <CardTitle>{g.name}</CardTitle>
          <div className="flex flex-col gap-6">
            {g.items.map((item) => (
              <SettingField key={`${item.key}:${item.updated_at ?? ''}:${String(item.is_default)}`} item={item} />
            ))}
          </div>
          </Card>
        </section>
      ))}
    </Page>
  );
}

/** The pre-send guard is its own role with its own prompt and Broker route. */
export const GUARD_GROUP = 'Охранник';

/** Shared rules for every persona (the guard has its own page). */
export function SettingsPage() {
  return (
    <SettingsView
      title="Общие правила"
      subtitle="Одинаковы для всех персонажей. Хранятся в базе: деплой их не сбрасывает."
      except={[GUARD_GROUP]}
    />
  );
}

/** What the guard page shows: the model and the instructions; Broker routes stay a backend fallback. */
export const GUARD_KEYS = ['guard_model', 'guard_rules'] as const;

/** The guard: checks every reply before it is sent, only the critical rules. */
export function GuardPage() {
  return (
    <SettingsView
      title="Охранник"
      subtitle="Проверяет каждый ответ перед отправкой. Правило честности на прямой вопрос «ты бот?» неизменяемое."
      only={[GUARD_GROUP]}
      keys={GUARD_KEYS}
    />
  );
}
