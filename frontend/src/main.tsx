import '@fontsource-variable/inter';
import './index.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app';
import { session } from './features/auth/session';
import { setUnauthorizedHandler } from './lib/api';
import { createQueryClient } from './lib/query';
import { applyTheme, watchSystemTheme } from './lib/theme';
import { createAppRouter } from './router';

const queryClient = createQueryClient();
const router = createAppRouter(queryClient);

// A request answered 401: drop the session and cached data, go to sign-in and come back after.
setUnauthorizedHandler(() => {
  session.clear();
  queryClient.clear();
  const { pathname, href } = router.state.location;
  if (!pathname.startsWith('/login')) void router.navigate({ to: '/login', search: { returnUrl: href } });
});
applyTheme();
watchSystemTheme();

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App router={router} queryClient={queryClient} />
  </StrictMode>,
);
