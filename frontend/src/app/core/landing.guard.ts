import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { AuthService, hasOwnerRights } from './auth.service';
import { Role } from './models';

export function defaultLanding(role: Role | undefined): string {
  return hasOwnerRights(role) ? '/studio' : '/inbox';
}

/** Resolve role only after restoring the session; never redirect an unknown owner to the inbox. */
export const landingGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.isAuthenticated()) return router.parseUrl(defaultLanding(auth.user()?.role));
  return auth.restore().pipe(map((ok) => router.parseUrl(ok ? defaultLanding(auth.user()?.role) : '/login')));
};
