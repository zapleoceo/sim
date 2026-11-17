import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { studioApi, type StudioDraft } from '@/lib/api/studio';

/** Private server draft only. This component cannot generate, reconcile or send. */
export function VideoDraft({ scope }: { scope: string }) {
  const [saved, setSaved] = useState<StudioDraft | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('Загружаем черновик…');
  const dirty = saved !== null && text !== saved.text;
  useEffect(() => {
    let active = true;
    void studioApi.draft().then((draft) => {
      if (!active) return;
      if (draft.scope !== scope) { setNotice('Образ Studio изменился. Обновите страницу.'); return; }
      setSaved(draft); setText(draft.text); setNotice('');
    }).catch(() => { if (active) setNotice('Черновик не загружен. Обновите страницу, чтобы повторить.'); });
    return () => { active = false; };
  }, [scope]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const save = async (next: string) => {
    if (!saved || busy) return;
    setBusy(true);
    try {
      const result = await studioApi.saveDraft({ ...saved, text: next });
      setSaved(result); setText(result.text);
      setNotice(next ? 'Черновик сохранён. Генерация не запускалась.' : 'Черновик удалён.');
    } catch {
      setNotice('Сохранить не удалось или версия изменилась. Ваш текст остался здесь; скопируйте его перед обновлением страницы.');
    } finally { setBusy(false); }
  };
  return <section className="flex flex-col gap-3 rounded-xl border bg-card p-3 sm:p-5" aria-label="Черновик кружочка">
    <h2 className="text-base font-semibold">Черновик кружочка</h2>
    <p className="text-sm text-muted-foreground">Можно сохранить текст, пока генерация выключена. Это не задание: после включения понадобится отдельная явная команда создания. Черновик относится к текущему образу Studio.</p>
    <Textarea aria-label="Текст черновика кружочка" value={text} maxLength={2000}
      disabled={!saved || busy} onChange={(event) => setText(event.target.value)} />
    <div className="flex flex-wrap gap-2">
      <Button disabled={!saved || busy || !dirty} onClick={() => void save(text)}>Сохранить черновик</Button>
      <Button variant="outline" disabled={!dirty || busy} onClick={() => {
        if (saved && window.confirm('Отменить несохранённые изменения?')) { setText(saved.text); setNotice('Изменения отменены.'); }
      }}>Отменить изменения</Button>
      <Button variant="outline" disabled={!saved || busy || (!saved.text && !text)} onClick={() => {
        if (window.confirm('Удалить сохранённый черновик и текущий текст?')) void save('');
      }}>Удалить черновик</Button>
    </div>
    {dirty && <p className="text-sm">Есть несохранённые изменения.</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
  </section>;
}
