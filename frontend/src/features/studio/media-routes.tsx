import type { StudioRoute } from '@/lib/api/studio';

export function MediaRoutes({ routes }: { routes: StudioRoute[] }) {
  if (!routes.length) return null;
  return (
    <details className="rounded-xl border p-4">
      <summary className="cursor-pointer font-medium">Планируемые маршруты медиа</summary>
      <p className="mt-3 text-sm text-muted-foreground">
        Эти предпочтения не управляют генерацией Studio выше. Выбор модели не запускает генерацию.
        Предпочтения сохраняются в общих настройках, в группе фото и кружочков.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {routes.map((route) => (
          <section key={route.kind} className="min-w-0 rounded-lg border p-3">
            <h2 className="font-medium">{route.label} · Недоступно</h2>
            <ol className="mt-2 space-y-1 text-sm">
              {route.chain.map((step, index) => (
                <li key={index} className="break-words">
                  {step.provider}: {step.model || 'модель не выбрана'}
                </li>
              ))}
            </ol>
            <p className="mt-2 text-sm text-muted-foreground">{route.reason}</p>
          </section>
        ))}
      </div>
    </details>
  );
}
