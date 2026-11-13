import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Field } from '@/components/field';
import { modelOptions } from '@/components/model-options';
import { OptionSelect } from '@/components/option-select';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { personasApi } from '@/lib/api/personas';
import { settingsApi } from '@/lib/api/settings';
import { formatDateTime } from '@/lib/format';
import type { AppSettingItem } from '@/lib/models';
import { qk } from '@/lib/query';
import { parseSettingValue, replaceSetting, settingHint } from './settings-logic';

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
export function SettingField({ item }: { item: AppSettingItem }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(String(item.value));
  const [error, setError] = useState('');
  const dirty = draft !== String(item.value);

  const apply = (updated: AppSettingItem, message: string): void => {
    queryClient.setQueryData<AppSettingItem[]>(qk.settings, (list) => (list ? replaceSetting(list, updated) : list));
    // The setting is a piece of every persona's prompt: the prompt maps are stale now.
    void queryClient.invalidateQueries({ queryKey: qk.personaPrompts });
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

