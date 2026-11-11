import '@fontsource-variable/inter';
import './index.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app';
import { createQueryClient } from './lib/query';
import { createAppRouter } from './router';
import { wireHttp } from './wiring';
import { applyTheme, watchSystemTheme } from './lib/theme';

const queryClient = createQueryClient();
const router = createAppRouter(queryClient);
wireHttp(router, queryClient);
applyTheme();
watchSystemTheme();

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App router={router} queryClient={queryClient} />
  </StrictMode>,
);
