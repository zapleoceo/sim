import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ErrorState } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { personasApi } from '@/lib/api/personas';
import { qk } from '@/lib/query';

/** Saved authoritative prompt and every application-owned addition, not provider internals. */
export function PersonaFullPrompt({ personaId }: { personaId: number }) {
  const [draft, setDraft] = useState<string | null>(null);
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.personaPrompt(personaId), queryFn: () => personasApi.prompt(personaId) });
  const save = useMutation({
    mutationFn: (text: string) => personasApi.savePrompt(personaId, text),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: qk.personaPrompt(personaId) }); setDraft(null); },
  });
  if (query.isError) return <ErrorState onRetry={() => void query.refetch()} />;
  if (!query.data) return <p>Загрузка промпта…</p>;
  const p = query.data;
  return (
    <section className="flex flex-col gap-3 text-sm" aria-label="Инструкции агента">
      <h3 className="font-semibold">Единый промпт агента</h3>
      <p role="status">{p.mode === 'unified'
        ? 'Активен единый промпт. Описание и инструкции стратегии больше не добавляются. Технические сообщения и динамические данные остаются отдельно.'
        : 'Активен совместимый режим: описание + стратегия + прежние правила. Текст ниже показывает их вместе; сохранение включает единый источник.'}</p>
      <Textarea aria-label="Единый промпт агента" value={draft ?? p.agent_prompt}
        onChange={(e) => setDraft(e.target.value)} disabled={save.isPending}
        className="min-h-96 font-mono text-xs" />
      <p className="text-xs text-muted-foreground">Несохранённый текст не отправляется модели. Эта кнопка сохраняет только единый промпт; остальные поля формы сохраняются отдельно.</p>
      <Button type="button" disabled={save.isPending || !(draft ?? p.agent_prompt).trim()}
        onClick={() => save.mutate(draft ?? p.agent_prompt)}>Сохранить единый промпт</Button>
      {save.isError && <p role="alert">Не удалось сохранить промпт. Текст сохранён в редакторе; повторите.</p>}
      {save.isSuccess && <p>Промпт сохранён.</p>}
      <details><summary>Сохранённое system-сообщение целиком</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap">{p.system}</pre></details>
      <details><summary>Дополнительные технические инструкции и обработка</summary>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap">{JSON.stringify(p.technical, null, 2)}</pre>
        <p>После проверки: опечатки {p.postprocessing.typo_percent}%, нижний регистр {p.postprocessing.lowercase_percent}%; код также убирает конечную точку. Голос синтезируется отдельно.</p>
        <p>Пример служебного напоминания, не фактический запрос:</p><pre className="whitespace-pre-wrap">{p.followup_example}</pre>
      </details>
      <details><summary>Контролёр: отдельный запрос</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap">{p.guard_system}</pre>
        <p>К нему отдельно добавляются черновик, управляющие поля, разрешённые ссылки и история. Реальные запросы видны под пробным ответом.</p>
      </details>
      <p className="text-xs text-muted-foreground">Граница: это сообщения SIM → AIbroker. Подписочный маршрут Broker оборачивает историю в JSON и добавляет инструкции продолжения и JSON-формата. Внутренние инструкции провайдера не доступны. Просмотр промпта не вызывает модель.</p>
    </section>
  );
}
