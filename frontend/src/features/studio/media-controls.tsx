import { useEffect, useState } from 'react';
import { Field } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { studioApi, type StudioOperation, type StudioState } from '@/lib/api/studio';

/** Unknown outcome allows only a read, never a second POST. The server also fences new IDs. */
export function MediaControls({ state }: { state: StudioState }) {
  const [kind, setKind] = useState<'speech' | 'video'>('speech');
  const [text, setText] = useState('');
  const [id, setId] = useState<string | null>(null);
  const [result, setResult] = useState<StudioOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [history, setHistory] = useState<StudioOperation[]>([]);
  const [historyReady, setHistoryReady] = useState(state.personal_access !== true);
  const [historyError, setHistoryError] = useState(false);
  useEffect(() => {
    if (!state.personal_access) return;
    let active = true;
    void studioApi.operations().then(({ operations }) => {
      if (!active) return;
      setHistory(operations);
      const pending = operations.find((operation) => operation.status === 'unknown');
      if (pending) {
        setId(pending.operation_id);
        setKind(pending.kind ?? 'speech');
        setResult(pending);
        setNotice('Восстановлена незавершённая операция. Повторная генерация заблокирована.');
      }
      setHistoryReady(true);
    }).catch(() => { if (active) setHistoryError(true); });
    return () => { active = false; };
  }, [state.personal_access]);
  const allowed = historyReady && state.capabilities_verified === true &&
    (kind === 'speech' ? state.speech_available === true : state.video_available === true);

  const remember = (next: StudioOperation) => {
    setResult(next);
    setHistory((previous) => [next, ...previous.filter((operation) =>
      operation.operation_id !== next.operation_id)].slice(0, 50));
  };

  const check = async (operationId: string) => {
    setBusy(true);
    try {
      const next = await studioApi.operation(operationId);
      remember(next);
      setNotice(next.status === 'complete' ? 'Результат сохранён.' :
        'Результат пока не подтверждён. Повторная генерация заблокирована.');
    } catch {
      setNotice('Статус получить не удалось. Не повторяйте генерацию; проверьте операцию позже.');
    } finally { setBusy(false); }
  };

  const generate = async () => {
    if (!allowed || busy || id || !text.trim()) return;
    const nextId = crypto.randomUUID();
    setId(nextId);
    setBusy(true);
    setNotice('Ожидаем результат…');
    try {
      await studioApi.media(nextId, kind, text);
      await check(nextId);
    } catch {
      setNotice('Результат не подтверждён. Повторная генерация заблокирована; можно проверить статус.');
    } finally { setBusy(false); }
  };

  const reconcile = async () => {
    if (!id || busy || !allowed) return;
    setBusy(true);
    try {
      const next = await studioApi.reconcile(id);
      remember(next);
      setNotice(next.status === 'complete' ? 'Результат сохранён.' : 'Результат не подтверждён. Новая генерация запрещена.');
    } catch { setNotice('Сверка недоступна. Исход неизвестен; повторная генерация запрещена.'); }
    finally { setBusy(false); }
  };

  return (
    <section aria-labelledby="studio-media-h" className="flex flex-col gap-3 rounded-xl border bg-card p-3 sm:p-5">
      <h2 id="studio-media-h" className="text-base font-semibold">Создать речь или видео</h2>
      <p className="text-sm text-muted-foreground">
        Генерация выключена до проверки личного доступа, возможностей и лимитов Broker.
        Подписочный чат не означает доступ к Google speech/video. Фото автоматически не передаётся.
      </p>
      <Field label="Тип результата">
        <select aria-label="Тип результата" value={kind} disabled={busy || !!id}
          onChange={(event) => setKind(event.target.value as 'speech' | 'video')}
          className="rounded-md border bg-background p-2">
          <option value="speech">Речь</option><option value="video">Видео</option>
        </select>
      </Field>
      <Field label="Текст для генерации">
        <Textarea value={text} onChange={(event) => setText(event.target.value)} maxLength={2000}
          disabled={!allowed || busy || !!id} />
      </Field>
      <Button onClick={() => void generate()} disabled={!allowed || busy || !!id || !text.trim()}>
        {busy ? 'Ожидаем…' : 'Создать медиа'}
      </Button>
      {id && <div className="flex flex-wrap items-center gap-2">
        <p className="break-all text-xs text-muted-foreground">Операция: {id}</p>
        <Button variant="outline" disabled={busy} onClick={() => void check(id)}>Проверить статус</Button>
        {result?.status !== 'complete' && <Button variant="outline" disabled={busy || !allowed}
          onClick={() => void reconcile()}>Сверить с Broker</Button>}
      </div>}
      {historyError && <p role="alert">История операций недоступна. Генерация заблокирована; обновите страницу позже.</p>}
      {history.length > 0 && <details>
        <summary>История операций · до 50 записей</summary>
        <ul className="flex flex-col gap-2 pt-2">
          {history.map((operation) => <li key={operation.operation_id} className="break-all text-sm">
            <Button variant="outline" className="h-auto max-w-full py-2 text-left break-all whitespace-normal" disabled={busy} onClick={() => {
              setId(operation.operation_id); setKind(operation.kind ?? 'speech'); setResult(operation); setText('');
              setNotice('Открыта сохранённая операция. Исходный текст в истории не хранится.');
            }}>Открыть {operation.operation_id}</Button>
            <span> · {operation.kind === 'video' ? 'Видео' : 'Речь'} · {operation.status === 'complete' ? 'Готово' : 'Исход неизвестен'}</span>
          </li>)}
        </ul>
      </details>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {result?.status === 'complete' && result.artifact_url && <div className="flex flex-col gap-2">
        <p className="text-sm">Синтетическое медиа · клиентам не отправлено</p>
        {kind === 'speech' ? <audio controls preload="none" src={result.artifact_url} aria-label="Созданная речь" /> :
          <video controls playsInline preload="none" src={result.artifact_url} aria-label="Созданное видео" className="max-h-96 w-full" />}
      </div>}
    </section>
  );
}
