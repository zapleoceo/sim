import { useQuery } from '@tanstack/react-query';
import { InlineError } from '@/components/empty-state';
import { FilterBar, FilterField, SearchInput } from '@/components/filter-bar';
import { DELIVERY_LABELS } from '@/components/message-card';
import { OptionSelect } from '@/components/option-select';
import { Input } from '@/components/ui/input';
import { channelsApi } from '@/lib/api/channels';
import { personasApi } from '@/lib/api/personas';
import type { Author, ChannelKind, Delivery, Direction } from '@/lib/models';
import { qk } from '@/lib/query';
import { CHANNEL_KINDS, CHANNEL_LABELS } from '@/lib/stages';
import {
  EMPTY_ACTIVITY_FORM, LIMITS, activityFilterCount, validateActivityForm, type ActivityFormValues,
} from './activity-filters-logic';

export function ActivityFilters({
  values,
  onChange,
  llm,
  clientScoped,
}: {
  values: ActivityFormValues;
  onChange: (v: ActivityFormValues) => void;
  llm: boolean;
  clientScoped: boolean;
}) {
  const channels = useQuery({ queryKey: qk.channels, queryFn: channelsApi.list });
  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const errors = validateActivityForm(values);
  const set = <K extends keyof ActivityFormValues>(key: K, value: ActivityFormValues[K]): void =>
    onChange({ ...values, [key]: value });

  const channelList = channels.data ?? [];
  const missingChannel =
    values.channel_id !== null && !channelList.some((c) => c.id === values.channel_id) ? values.channel_id : null;

  return (
    <div className="flex flex-col gap-2">
      <FilterBar
        alwaysSheet
        activeCount={activityFilterCount(values)}
        onReset={() => onChange(EMPTY_ACTIVITY_FORM)}
        search={
          <SearchInput
            value={values.q}
            maxLength={LIMITS.q}
            onChange={(q) => set('q', q)}
            placeholder={llm ? 'Модель, провайдер или назначение' : 'Клиент или текст сообщения'}
          />
        }
        filters={
          <>
            <FilterField label="Тип канала">
              <OptionSelect<ChannelKind | ''>
                value={values.channel_kind}
                onChange={(v) => set('channel_kind', v)}
                options={[{ value: '', label: 'Все' }, ...CHANNEL_KINDS.map((k) => ({ value: k, label: CHANNEL_LABELS[k] }))]}
              />
            </FilterField>
            <FilterField label="Аккаунт / канал">
              <OptionSelect<number | null>
                value={values.channel_id}
                onChange={(v) => set('channel_id', v)}
                options={[
                  { value: null, label: 'Все' },
                  ...(missingChannel !== null ? [{ value: missingChannel, label: `Канал № ${missingChannel}` }] : []),
                  ...channelList.map((c) => ({ value: c.id, label: `${c.name} · ${c.account_label || CHANNEL_LABELS[c.kind]}` })),
                ]}
              />
            </FilterField>
            <FilterField label="Персонаж">
              <OptionSelect<number | null>
                value={values.persona_id}
                onChange={(v) => set('persona_id', v)}
                options={[{ value: null, label: 'Все' }, ...(personas.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
              />
            </FilterField>
            {!clientScoped && (
              <FilterField label="ID клиента">
                <Input
                  type="number"
                  min={1}
                  inputMode="numeric"
                  value={values.client_id}
                  aria-invalid={errors.client_id ? true : undefined}
                  onChange={(e) => set('client_id', e.target.value)}
                />
                {errors.client_id && <span className="text-xs text-destructive">{errors.client_id}</span>}
              </FilterField>
            )}
            {llm ? (
              <>
                <FilterField label="Модель (точно)">
                  <Input value={values.model} maxLength={LIMITS.model} onChange={(e) => set('model', e.target.value)} />
                </FilterField>
                <FilterField label="Провайдер (точно)">
                  <Input value={values.provider} maxLength={LIMITS.provider} onChange={(e) => set('provider', e.target.value)} />
                </FilterField>
                <FilterField label="Назначение (точно)">
                  <Input value={values.workflow} maxLength={LIMITS.workflow} onChange={(e) => set('workflow', e.target.value)} />
                </FilterField>
                <FilterField label="Результат">
                  <OptionSelect<string | null>
                    value={values.has_error === null ? null : String(values.has_error)}
                    onChange={(v) => set('has_error', v === null ? null : v === 'true')}
                    options={[
                      { value: null, label: 'Все' },
                      { value: 'false', label: 'Успешно' },
                      { value: 'true', label: 'Ошибка' },
                    ]}
                  />
                </FilterField>
              </>
            ) : (
              <>
                <FilterField label="Направление">
                  <OptionSelect<Direction | ''>
                    value={values.direction}
                    onChange={(v) => set('direction', v)}
                    options={[
                      { value: '', label: 'Все' },
                      { value: 'in', label: 'Входящие' },
                      { value: 'out', label: 'Исходящие' },
                    ]}
                  />
                </FilterField>
                <FilterField label="Автор">
                  <OptionSelect<Author | ''>
                    value={values.author}
                    onChange={(v) => set('author', v)}
                    options={[
                      { value: '', label: 'Все' },
                      { value: 'client', label: 'Клиент' },
                      { value: 'ai', label: 'ИИ' },
                      { value: 'operator', label: 'Оператор' },
                    ]}
                  />
                </FilterField>
                <FilterField label="Доставка">
                  <OptionSelect<Delivery | ''>
                    value={values.delivery}
                    onChange={(v) => set('delivery', v)}
                    options={[
                      { value: '', label: 'Все' },
                      ...(Object.keys(DELIVERY_LABELS) as Delivery[]).map((d) => ({ value: d, label: DELIVERY_LABELS[d] })),
                    ]}
                  />
                </FilterField>
              </>
            )}
            <FilterField label="С (UTC)">
              <Input type="date" value={values.from} onChange={(e) => set('from', e.target.value)} />
            </FilterField>
            <FilterField label="По включительно (UTC)">
              <Input type="date" value={values.to} onChange={(e) => set('to', e.target.value)} />
            </FilterField>
          </>
        }
      />
      {(channels.isError || personas.isError) && (
        <InlineError>Не удалось загрузить список каналов или персонажей. Остальные фильтры доступны.</InlineError>
      )}
      {errors.dateOrder && <InlineError>{errors.dateOrder}</InlineError>}
      {errors.client_id && <InlineError>ID клиента: {errors.client_id}</InlineError>}
    </div>
  );
}
