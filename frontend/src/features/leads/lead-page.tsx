import { useQuery } from '@tanstack/react-query';
import { Link, getRouteApi } from '@tanstack/react-router';
import { AlertTriangle, MessagesSquare, Save } from 'lucide-react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { Avatar } from '@/components/avatar';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Field, Section } from '@/components/field';
import { BackLink, Page } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { lastMessagePreview } from '@/features/chats/timeline-logic';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';
import { formatDateTime, formatShort } from '@/lib/format';
import { qk } from '@/lib/query';
import type { ClientDetail, ClientPatch } from '@/lib/types';
import { clientName, positiveInt } from '@/lib/utils';
import { leadsApi, useUpdateClient } from './api';
import { ClientAiSwitch } from './client-ai-switch';
import { PassportEditor } from './passport-editor';

const route = getRouteApi('/_app/leads/$clientId');

/** How the lead is shown in lists, phone, operator notes. What she knows about him is the passport. */
interface ProfileValues { name: string; phone: string; notes: string }

const toValues = (c: ClientDetail): ProfileValues => ({ name: c.name, phone: c.phone ?? '', notes: c.notes });

export function profilePatch(values: ProfileValues, initial: ProfileValues): ClientPatch {
  const patch: ClientPatch = {};
  if (values.name !== initial.name) patch.name = values.name.trim();
  if (values.phone !== initial.phone) patch.phone = values.phone.trim() || null;
  if (values.notes !== initial.notes) patch.notes = values.notes;
  return patch;
}

function ProfileForm({ client }: { client: ClientDetail }) {
  const form = useForm<ProfileValues>({ defaultValues: toValues(client) });
  const { register, handleSubmit, formState, reset } = form;
  const update = useUpdateClient(client.id, () => 'Карточка лида сохранена');
  useUnsavedGuard(formState.isDirty);

  // A live update (e.g. the name changed elsewhere) refreshes the form unless it is being edited.
  useEffect(() => {
    if (!formState.isDirty) reset(toValues(client));
  }, [client, formState.isDirty, reset]);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={handleSubmit((values) => {
        const patch = profilePatch(values, (formState.defaultValues ?? toValues(client)) as ProfileValues);
        update.mutate(patch, { onSuccess: () => reset(values) });
      })}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="client-name" label="Имя в списках" hint="Как лид подписан в дашборде.">
          <Input id="client-name" autoComplete="off" {...register('name')} />
        </Field>
        <Field id="client-phone" label="Телефон">
          <Input id="client-phone" type="tel" autoComplete="off" {...register('phone')} />
        </Field>
      </div>
      <Field id="client-notes" label="Заметки оператора" hint="Агент видит их в карточке клиента.">
        <Textarea id="client-notes" className="max-h-80 min-h-20" {...register('notes')} />
      </Field>
      <div className="flex justify-end">
        <Button type="submit" disabled={!formState.isDirty || update.isPending}>
          <Save />
          Сохранить
        </Button>
      </div>
    </form>
  );
}

function LeadCard({ client }: { client: ClientDetail }) {
  const name = clientName(client);
  return (
    <>
      <header className="flex flex-wrap items-center gap-3">
        <Avatar name={name} size={48} />
        <div className="flex min-w-0 flex-1 flex-col">
          <h1 className="truncate text-lg font-semibold sm:text-2xl">{name}</h1>
          <p className="text-sm text-muted-foreground">
            {[
              client.username && `@${client.username}`,
              client.persona && `лид персонажа ${client.persona.name}`,
              client.last_seen_at && `был(а) ${formatDateTime(client.last_seen_at)}`,
            ].filter(Boolean).join(' · ') || 'нет данных'}
          </p>
        </div>
        <ClientAiSwitch clientId={client.id} enabled={client.ai_enabled} name={name} withLabel />
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Section
            id="passport"
            title="Паспорт"
            description="Живая анкета человека. Паспортистка обновляет её после каждого ответа и звонка; поля, которые вы поправили, она не трогает."
          >
            <PassportEditor clientId={client.id} passport={client.passport} />
          </Section>
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <Section id="profile" title="Профиль">
            <ProfileForm client={client} />
          </Section>
          <Section id="chats" title="Чаты">
            {client.chats.length === 0 ? (
              <EmptyState icon={MessagesSquare} title="Чатов нет" className="py-4" />
            ) : (
              <ul className="flex flex-col divide-y">
                {client.chats.map((chat) => (
                  <li key={chat.id}>
                    <Link
                      to="/chats"
                      search={{ id: chat.id }}
                      className="flex items-center gap-3 rounded-md px-1 py-2.5 hover:bg-accent/60"
                    >
                      <MessagesSquare className="size-4 flex-none text-muted-foreground" />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-sm">{lastMessagePreview(chat.last_message)}</span>
                        <span className="text-xs text-muted-foreground">
                          {chat.persona ? `${chat.persona.name} · ` : ''}
                          {formatShort(chat.last_message_at) || 'без сообщений'}
                          {!chat.ai_enabled && ' · ИИ в чате выключен'}
                        </span>
                      </span>
                      {chat.needs_operator && <AlertTriangle className="size-4 flex-none text-warning" aria-label="Нужен оператор" />}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}

/** The lead card: passport, profile and notes, AI switch, the lead's chats. */
export function LeadPage() {
  const { clientId } = route.useParams();
  const id = positiveInt(clientId) ?? 0;
  const client = useQuery({ queryKey: qk.client(id), queryFn: () => leadsApi.get(id), enabled: id > 0 });

  return (
    <Page width="wide" className="max-w-[1600px]">
      <BackLink to="/leads">Все лиды</BackLink>
      {id === 0 && <ErrorState title="Лид не найден" />}
      {id > 0 && client.isPending && (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-12 w-64" />
          <Skeleton className="h-60 w-full rounded-xl" />
        </div>
      )}
      {client.isError && <ErrorState title="Лид не найден" onRetry={() => void client.refetch()} />}
      {client.data && <LeadCard client={client.data} />}
    </Page>
  );
}
