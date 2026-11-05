import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map, of } from 'rxjs';
import { AuthService } from './auth.service';

export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.isAuthenticated()) return true;
  return auth.restore().pipe(
    map((ok) => (ok ? true : router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } }))),
  );
};

export const superadminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.isSuperadmin() ? of(true) : of(router.createUrlTree(['/inbox']));
};

export const ownerGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.isOwner() ? of(true) : of(router.createUrlTree(['/inbox']));
};
