import { useMutation } from '@tanstack/react-query';
import { Upload } from 'lucide-react';
import { useState, type DragEvent } from 'react';
import { InlineError } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { analyticsApi } from '@/lib/api/analytics';
import { cn } from '@/lib/utils';
import { CSV_HEADER, csvFileError } from './partner-logic';

export function PartnerPage() {
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [error, setError] = useState('');

  const upload = useMutation({
    mutationFn: (f: File) => analyticsApi.importPartnerCsv(f),
    onSuccess: () => setFile(null),
  });

  const pick = (f: File | null | undefined): void => {
    setError('');
    if (!f) return;
    const problem = csvFileError(f);
    if (problem) {
      setError(problem);
      setFile(null);
      return;
    }
    setFile(f);
  };

  const onDrop = (e: DragEvent<HTMLLabelElement>): void => {
    e.preventDefault();
    setOver(false);
    pick(e.dataTransfer.files[0]);
  };

  const r = upload.data;

  return (
    <Page width="narrow">
      <PageHeader title="Партнёр" subtitle="Импорт регистраций и оплат из CSV-выгрузки партнёра" />

      <Card>
        <CardTitle>Формат файла</CardTitle>
        <CardDescription>Заголовок и колонки по порядку:</CardDescription>
        <code className="block overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs whitespace-nowrap">{CSV_HEADER}</code>
        <p className="text-sm text-muted-foreground">
          <strong className="text-foreground">event</strong> — registration или payment;{' '}
          <strong className="text-foreground">client_ref</strong> — код из ссылки регистрации (?ref=…). Повторный импорт
          безопасен: записи с тем же external_id пропускаются.
        </p>
      </Card>

      <Card>
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          className={cn(
            'relative flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed p-8 text-center text-sm text-muted-foreground transition-colors focus-within:border-primary hover:bg-accent/40',
            over && 'border-primary bg-accent',
          )}
        >
          <Upload className="size-8" />
          <span>{file ? file.name : 'Перетащите CSV сюда или выберите файл'}</span>
          <input
            type="file"
            accept=".csv,text/csv"
            aria-label="CSV-файл партнёра"
            className="sr-only"
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
        </label>
        <InlineError>{error}</InlineError>
        <div>
          <Button disabled={!file || upload.isPending} onClick={() => file && upload.mutate(file)}>
            {upload.isPending ? 'Импортируем…' : 'Импортировать'}
          </Button>
        </div>
      </Card>

      {r && (
        <Card aria-live="polite">
          <CardTitle>Результат</CardTitle>
          <dl className="flex flex-wrap gap-8">
            <div className="flex flex-col-reverse">
              <dt className="text-sm text-muted-foreground">импортировано</dt>
              <dd className="text-2xl font-semibold text-success tabular">{r.imported}</dd>
            </div>
            <div className="flex flex-col-reverse">
              <dt className="text-sm text-muted-foreground">пропущено</dt>
              <dd className="text-2xl font-semibold tabular">{r.skipped}</dd>
            </div>
            <div className="flex flex-col-reverse">
              <dt className="text-sm text-muted-foreground">ошибок</dt>
              <dd className={cn('text-2xl font-semibold tabular', r.errors.length > 0 && 'text-destructive')}>{r.errors.length}</dd>
            </div>
          </dl>
          {r.errors.length > 0 && (
            <ul className="max-h-72 list-disc overflow-y-auto pl-5 text-sm text-destructive">
              {r.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </Page>
  );
}
