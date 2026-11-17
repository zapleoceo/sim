import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { useState } from 'react';
import { useLocation } from '@tanstack/react-router';
import { useConfirm } from '@/components/confirm';
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
import { parseSettingValue, replaceSetting, settingHint, settingAnchor, SETTINGS_DRAFTS, type SettingDraft } from './settings-logic';

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

/** Memory-only drafts survive navigation/refetch; no private prompts in browser storage. */
export function SettingField({ item }: { item: AppSettingItem }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const hash = useLocation({ select: (location) => location.hash });
  const draftKey = [...SETTINGS_DRAFTS, item.key];
  const mutationKey = ['settings-editor-write', item.key];
  const busy = useIsMutating({ mutationKey }) > 0;
  const stored = useQuery<SettingDraft | null>({ queryKey: draftKey, queryFn: () => null,
    initialData: null, enabled: false, gcTime: Infinity });
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const draft = stored.data?.value ?? String(item.value);
  const dirty = draft !== String(item.value);
  const conflict = dirty && stored.data?.base !== String(item.value);
  const expanded = item.kind !== 'text' || open || dirty || hash === settingAnchor(item.key);
  const discard = (): void => { queryClient.setQueryData(draftKey, null); setError(''); };
  const setDraft = (value: string): void => {
    queryClient.setQueryData<SettingDraft | null>(draftKey, (previous): SettingDraft | null =>
      value === String(item.value) ? null : { value, base: previous?.base ?? String(item.value) });
    setError('');
  };
  const apply = (updated: AppSettingItem, message: string): void => {
    queryClient.setQueryData<AppSettingItem[]>(qk.settings, (list) => list ? replaceSetting(list, updated) : list);
    discard();
    toast.success(message);
  };
  const save = useMutation({ mutationKey, mutationFn: (value: string | number) => settingsApi.save(item.key, value),
    onSuccess: (updated) => apply(updated, 'Сохранено'),
    onError: () => setError('Не удалось сохранить. Ваш текст оставлен в редакторе; попробуйте ещё раз.') });
  const reset = useMutation({ mutationKey, mutationFn: () => settingsApi.reset(item.key),
    onSuccess: (updated) => apply(updated, 'Возвращено значение по умолчанию'),
    onError: () => setError('Не удалось сбросить настройку. Ваш текст оставлен в редакторе.') });
  const onSave = async (): Promise<void> => {
    const parsed = parseSettingValue(item, draft);
    if (!parsed.ok) { setError(parsed.error); return; }
    if (conflict && !await confirm({ title: 'Настройка изменилась на сервере',
      message: 'Сохранить ваш вариант поверх нового значения?', confirmLabel: 'Сохранить мой вариант' })) return;
    setError('');
    save.mutate(parsed.value);
  };
  return <div className="flex min-w-0 flex-col gap-2 rounded-lg border p-3 sm:p-4" data-setting={item.key}>
    {!expanded && <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{item.label}</h3>
        <Button variant="outline" size="sm" aria-label={'Изменить: ' + item.label} onClick={() => setOpen(true)}>Изменить</Button>
      </div>
      <p className="line-clamp-2 whitespace-pre-wrap text-sm text-muted-foreground [overflow-wrap:anywhere]">{draft || 'Не задано'}</p>
    </>}
    {expanded && <fieldset disabled={busy} className="min-w-0">
      <Field label={item.label} hint={settingHint(item)} error={error}>
        {item.kind === 'model' ? <ModelInput value={draft} onChange={setDraft} />
          : item.kind === 'text' ? <Textarea value={draft} onChange={(e) => setDraft(e.target.value)}
            autoFocus={open} className="min-h-48 max-h-[65vh] font-mono text-sm" />
            : <Input value={draft} onChange={(e) => setDraft(e.target.value)} inputMode={item.kind === 'int' ? 'numeric' : undefined}
              className={item.kind === 'int' ? 'max-w-40 tabular' : undefined} />}
      </Field>
    </fieldset>}
    {conflict && <p role="status" className="text-sm text-amber-700">На сервере появилось другое значение. Ваши правки сохранены; выберите, какой вариант оставить.</p>}
    {!expanded && error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={item.is_default ? 'muted' : 'warning'}>{item.is_default ? 'По умолчанию' : 'Настроено'}</Badge>
      {!item.is_default && item.updated_at && <span className="text-xs text-muted-foreground">{formatDateTime(item.updated_at)}</span>}
      {dirty && <span role="status" className="text-xs text-muted-foreground">Не сохранено</span>}
      <span className="flex-1" />
      {!item.is_default && <Button variant="ghost" size="sm" disabled={busy} onClick={async () => {
        if (await confirm({ title: 'Сбросить «' + item.label + '»?', message: 'Будет возвращено значение по умолчанию. Несохранённые правки этого поля будут потеряны.', confirmLabel: 'Сбросить настройку' })) reset.mutate();
      }}><RotateCcw />Вернуть по умолчанию</Button>}
      {dirty && <>
        <Button variant="ghost" size="sm" disabled={busy} onClick={discard}>{conflict ? 'Загрузить сохранённое' : 'Отменить правки'}</Button>
        <Button size="sm" disabled={busy} onClick={() => void onSave()}>Сохранить</Button>
      </>}
      {item.kind === 'text' && open && !dirty && hash !== settingAnchor(item.key)
        && <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Свернуть</Button>}
    </div>
  </div>;
}
