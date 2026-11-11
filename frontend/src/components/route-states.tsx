import type { ErrorComponentProps } from '@tanstack/react-router';
import { ErrorState } from './empty-state';

/** Thin progress line while a lazy page chunk loads. */
export function RoutePending() {
  return (
    <div role="progressbar" aria-label="Загрузка" className="fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden bg-primary/15">
      <div className="h-full w-1/3 animate-[sim-progress_1s_ease-in-out_infinite] bg-primary" />
    </div>
  );
}

/** A page crashed or its chunk failed to load (e.g. after a deploy): offer a reload. */
export function RouteError({ error, reset }: ErrorComponentProps) {
  console.error('[route]', error);
  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <ErrorState
        title="Страница не открылась"
        onRetry={() => {
          reset();
          window.location.reload();
        }}
      />
    </div>
  );
}
