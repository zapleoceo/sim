import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { ShieldCheck, Trash2, UserPlus } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { Avatar } from '@/components/avatar';
import { useConfirm } from '@/components/confirm';
import { DataTable } from '@/components/data-table';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Field } from '@/components/field';
import { OptionSelect } from '@/components/option-select';
import { Page, PageHeader } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { adminsApi } from '@/lib/api/admins';
import { useAuth } from '@/lib/auth';
import type { AdminPatch, AdminUser, Role } from '@/lib/models';
import { ROLE_LABELS } from '@/lib/nav';
import { qk } from '@/lib/query';
import { upsertById } from '@/lib/utils';
import { ROLES, adminName, adminTargetError, parseAdminTarget } from './admins-logic';

const roleOptions = ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }));

export function AdminsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const meId = useAuth().user?.id ?? null;
  const [target, setTarget] = useState('');
  const [role, setRole] = useState<Role>('operator');
  const [tried, setTried] = useState(false);

  const admins = useQuery({ queryKey: qk.admins, queryFn: adminsApi.list });
  const put = (a: AdminUser): void => {
    queryClient.setQueryData<AdminUser[]>(qk.admins, (list) => upsertById(list ?? [], a));
  };

  const create = useMutation({
    mutationFn: () => adminsApi.create({ ...parseAdminTarget(target), role }),
    onSuccess: (a) => {
      put(a);
      setTarget('');
      setTried(false);
      toast.success('Администратор добавлен');
    },
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: AdminPatch }) => adminsApi.update(id, body),
    onSuccess: put,
  });
  const remove = useMutation({
    mutationFn: (a: AdminUser) => adminsApi.remove(a.id),
    onSuccess: (_r, a) => {
      queryClient.setQueryData<AdminUser[]>(qk.admins, (list) => list?.filter((x) => x.id !== a.id));
      toast.success('Администратор удалён');
    },
  });

  const targetError = tried ? adminTargetError(target) : '';
  const add = (e: FormEvent): void => {
    e.preventDefault();
    setTried(true);
    if (adminTargetError(target)) return;
    create.mutate();
  };

  const askRemove = async (a: AdminUser): Promise<void> => {
    const ok = await confirm({
      title: 'Удалить администратора?',
      message: `${adminName(a)} потеряет доступ к админке.`,
      confirmLabel: 'Удалить',
      danger: true,
    });
    if (ok) remove.mutate(a);
  };

  const columns: ColumnDef<AdminUser, unknown>[] = [
    {
      id: 'admin',
      header: 'Администратор',
      cell: ({ row: { original: a } }) => (
        <span className="flex min-w-0 items-center gap-2.5">
          <Avatar name={adminName(a)} url={a.photo_url} size={30} />
          <span className="min-w-0">
            <span className="block truncate font-medium">
              {adminName(a)}
              {a.id === meId && <span className="ml-1 font-normal text-muted-foreground">(вы)</span>}
            </span>
            <span className="block truncate text-xs text-muted-foreground md:hidden">
              {a.tg_username ? `@${a.tg_username}` : (a.telegram_id ?? 'ждёт первого входа')}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: 'username',
      header: 'Username',
      cell: ({ row }) => (row.original.tg_username ? `@${row.original.tg_username}` : '—'),
      meta: { className: 'hidden md:table-cell' },
    },
    {
      id: 'telegram_id',
      header: 'Telegram ID',
      cell: ({ row }) => <span className="tabular">{row.original.telegram_id ?? 'ждёт первого входа'}</span>,
      meta: { className: 'hidden lg:table-cell' },
    },
    {
      id: 'role',
      header: 'Роль',
      cell: ({ row: { original: a } }) => (
        <OptionSelect<Role>
          aria-label={`Роль: ${adminName(a)}`}
          className="h-8 w-36"
          value={a.role}
          disabled={a.id === meId}
          options={roleOptions}
          onChange={(r) => r !== a.role && patch.mutate({ id: a.id, body: { role: r } })}
        />
      ),
    },
    {
      id: 'status',
      header: 'Статус',
      cell: ({ row }) => (
        <Badge variant={row.original.is_active ? 'success' : 'muted'}>{row.original.is_active ? 'Активен' : 'Отключён'}</Badge>
      ),
      meta: { className: 'hidden sm:table-cell' },
    },
    {
      id: 'actions',
      header: () => <span className="sr-only">Действия</span>,
      cell: ({ row: { original: a } }) =>
        a.id === meId ? null : (
          <div className="flex items-center justify-end gap-1">
            <Button variant="ghost" size="sm" onClick={() => patch.mutate({ id: a.id, body: { is_active: !a.is_active } })}>
              {a.is_active ? 'Отключить' : 'Включить'}
            </Button>
            <Button variant="ghost-destructive" size="icon-sm" aria-label={`Удалить ${adminName(a)}`} onClick={() => void askRemove(a)}>
              <Trash2 />
            </Button>
          </div>
        ),
      meta: { className: 'text-right' },
    },
  ];

  return (
    <Page>
      <PageHeader title="Администраторы" subtitle="Вход в админку только через Telegram: добавьте Telegram ID или @username" />

      <Card>
        <form onSubmit={add} noValidate className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <Field label="Telegram ID или @username" error={targetError} className="flex-1">
            <Input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="123456789 или @username" autoComplete="off" />
          </Field>
          <Field label="Роль" className="sm:w-44">
            <OptionSelect<Role> value={role} onChange={setRole} options={roleOptions} />
          </Field>
          <Button type="submit" disabled={create.isPending} className="sm:mt-[1.375rem]">
            <UserPlus />
            Добавить
          </Button>
        </form>
      </Card>

      {admins.isError ? (
        <ErrorState onRetry={() => void admins.refetch()} />
      ) : (
        <Card className="gap-0 p-0 sm:p-0">
          <DataTable
            columns={columns}
            data={admins.data ?? []}
            loading={admins.isPending}
            getRowId={(a) => String(a.id)}
            caption="Администраторы"
            rowClassName={(row) => (row.original.is_active ? undefined : 'opacity-60')}
            empty={<EmptyState icon={ShieldCheck} title="Администраторов нет" hint="Добавьте первого выше." />}
          />
        </Card>
      )}
    </Page>
  );
}
