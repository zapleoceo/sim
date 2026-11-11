import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { useEffect } from 'react';
import { Toaster } from 'sonner';
import { ConfirmProvider } from './components/confirm';
import { TooltipProvider } from './components/ui/tooltip';
import { titleFor, type createAppRouter } from './router';
import { useTheme } from './lib/theme';

function ThemedToaster() {
  const mode = useTheme();
  return <Toaster theme={mode} position="top-center" richColors closeButton duration={4000} />;
}

export function App({ router, queryClient }: { router: ReturnType<typeof createAppRouter>; queryClient: QueryClient }) {
  useEffect(
    () =>
      router.subscribe('onResolved', ({ toLocation }) => {
        document.title = titleFor(toLocation.pathname);
      }),
    [router],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <ConfirmProvider>
          <RouterProvider router={router} />
          <ThemedToaster />
        </ConfirmProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
