import { Link } from '@tanstack/react-router';
import { IdCard } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { formatDateTime } from '@/lib/format';
import type { Client } from '@/lib/types';
import { clientName } from '@/lib/utils';
import { PASSPORT_FIELDS, passportPreview, stageInfo } from './passport-logic';

/** The short passport: stage, temperature, the known fields and the first lines of the text. */
export function PassportSummary({ client }: { client: Client }) {
  const p = client.passport;
  const stage = stageInfo(p.stage);
  const facts = PASSPORT_FIELDS.filter(({ field }) => !['stage', 'temperature'].includes(field))
    .map(({ field, label }) => ({ label, value: p[field] }))
    .filter(({ value }) => value !== null && value !== '');
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={stage.variant}>{stage.label}</Badge>
        <span className="text-muted-foreground">теплота {p.temperature}/100</span>
      </div>
      {facts.length > 0 && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          {facts.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-muted-foreground">{f.label}</dt>
              <dd className="break-words">{String(f.value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="whitespace-pre-wrap break-words">{p.text ? passportPreview(p.text) : 'Паспорт ещё пустой: паспортистка заполнит его после первых ответов.'}</p>
      <p className="text-xs text-muted-foreground">
        {p.updated_at ? `Обновлён ${formatDateTime(p.updated_at)}` : 'Ещё не обновлялся'}
      </p>
      <Link to="/leads/$clientId" params={{ clientId: String(client.id) }} className="text-sm font-medium text-primary hover:underline">
        Открыть карточку лида и править паспорт
      </Link>
    </div>
  );
}

/** «Паспорт» in the chat header: the short passport in a side panel. */
export function PassportSheet({ client }: { client: Client }) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="ghost" size="sm">
          <IdCard />
          <span className="max-sm:sr-only">Паспорт</span>
        </Button>
      </SheetTrigger>
      <SheetContent className="overflow-y-auto p-4 sm:max-w-md">
        <SheetHeader className="p-0 pr-8">
          <SheetTitle>Паспорт: {clientName(client)}</SheetTitle>
          <SheetDescription>Что персонаж знает о человеке. Агент читает это перед каждым ответом.</SheetDescription>
        </SheetHeader>
        <PassportSummary client={client} />
      </SheetContent>
    </Sheet>
  );
}
