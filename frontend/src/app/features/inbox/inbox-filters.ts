import { FormControl, FormGroup } from '@angular/forms';
import { ChannelKind, ClientStatus, DialogFilter, Stage } from '../../core/models';

export const DEFAULT_DIALOG_FILTER: DialogFilter = {
  channel_kind: '',
  persona_id: null,
  stage: '',
  status: '',
  q: '',
  unread_only: false,
};

export type InboxFilterForm = FormGroup<{
  channel_kind: FormControl<ChannelKind | ''>;
  persona_id: FormControl<number | null>;
  stage: FormControl<Stage | ''>;
  status: FormControl<ClientStatus | ''>;
  q: FormControl<string>;
  unread_only: FormControl<boolean>;
}>;

/** Единая форма фильтров инбокса: одно место для валидации, сброса и сериализации в запрос. */
export function createInboxFilterForm(initial: DialogFilter = DEFAULT_DIALOG_FILTER): InboxFilterForm {
  return new FormGroup({
    channel_kind: new FormControl<ChannelKind | ''>(initial.channel_kind, { nonNullable: true }),
    persona_id: new FormControl<number | null>(initial.persona_id),
    stage: new FormControl<Stage | ''>(initial.stage, { nonNullable: true }),
    status: new FormControl<ClientStatus | ''>(initial.status, { nonNullable: true }),
    q: new FormControl<string>(initial.q, { nonNullable: true }),
    unread_only: new FormControl<boolean>(initial.unread_only, { nonNullable: true }),
  });
}

/** Сколько фильтров отличается от значений по умолчанию (для бейджа «Фильтры»). */
export function activeFilterCount(filter: DialogFilter): number {
  return (Object.keys(DEFAULT_DIALOG_FILTER) as (keyof DialogFilter)[]).filter((k) => {
    const v = filter[k];
    return typeof v === 'string' ? v.trim() !== '' : v !== DEFAULT_DIALOG_FILTER[k];
  }).length;
}

/** Нормализованное значение для запроса (q обрезан). */
export function normalizeFilter(filter: DialogFilter): DialogFilter {
  return { ...filter, q: filter.q.trim() };
}
