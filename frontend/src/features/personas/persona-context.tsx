import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ErrorState } from '@/components/empty-state';
import { http } from '@/lib/api/http';

interface Payload { content: unknown; truncated: boolean }
interface ContextView {
  persona_id: number; notice: string; snapshot: Payload | null; example_context: Payload;
  layers: { key: string; title: string; source: string; field: string; payload: Payload }[];
}
const TEXT = 'max-h-96 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs [overflow-wrap:anywhere]';

export function PersonaContext({ personaId }: { personaId: number }) {
  const [input, setInput] = useState('');
  const [dialog, setDialog] = useState<number | null>(null);
  const query = useQuery({ queryKey: ['persona-context', personaId, dialog],
    queryFn: () => http.get<ContextView>(`/api/personas/${personaId}/context`, { params: dialog ? { dialog_id: dialog } : {} }) });
  return <section aria-label="Все инструкции и контекст" className="space-y-3 rounded-lg border p-3">
    <h3 className="font-medium">Все инструкции и контекст</h3>
    <p className="text-xs text-muted-foreground">Сохранённые слои показаны отдельно. Для данных клиента выберите номер его диалога с этим персонажем. Просмотр доступен владельцу.</p>
    <form className="flex flex-wrap items-end gap-2" onSubmit={e => { e.preventDefault(); setDialog(Number(input) || null); }}>
      <label className="text-xs">Номер диалога<Input type="number" min={1} value={input} onChange={e => setInput(e.target.value)} /></label>
      <Button size="sm" type="submit">Показать контекст</Button>
      <Button size="sm" type="button" variant="outline" onClick={() => { setDialog(null); setInput(''); }}>Без данных клиента</Button>
    </form>
    {query.isPending ? <p role="status">Собираем сохранённый контекст…</p> : query.isError ?
      <ErrorState title="Контекст недоступен. Проверьте номер диалога и доступ." onRetry={() => void query.refetch()} /> : <>
        <p className="text-xs text-muted-foreground">{query.data.notice}</p>
        {query.data.layers.map(layer => <details key={layer.key}>
          <summary className="cursor-pointer text-sm">{layer.title} · {layer.source === 'code' ? 'правила кода' : layer.source === 'setting' ? 'настройки' : layer.source === 'strategy' ? 'сценарий' : 'персонаж'}</summary>
          <p className="text-xs text-muted-foreground">Источник: {layer.field || 'только чтение'}</p>
          {layer.payload.truncated && <p>Текст усечён по лимиту просмотра.</p>}
          <pre className={TEXT}>{typeof layer.payload.content === 'string' ? layer.payload.content : JSON.stringify(layer.payload.content, null, 2)}</pre>
        </details>)}
        <details key={dialog ?? 'example'}><summary className="cursor-pointer">{dialog ? `Сборка для диалога № ${dialog}` : 'Пример динамических фактов без данных клиента'}</summary>
          {(query.data.snapshot ?? query.data.example_context).truncated && <p>Контекст усечён по лимиту просмотра.</p>}
          <pre className={TEXT}>{JSON.stringify((query.data.snapshot ?? query.data.example_context).content, null, 2)}</pre>
        </details>
      </>}
  </section>;
}
