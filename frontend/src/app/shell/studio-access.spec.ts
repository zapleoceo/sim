import { visibleNav } from './nav';
import { routes } from '../app.routes';
import { ownerGuard } from '../core/guards';
import { defaultLanding, landingGuard } from '../core/landing.guard';

describe('Studio access', () => {
  it('shows the studio only to owners and superadmins', () => {
    expect(visibleNav('owner').some((item) => item.path === '/studio')).toBeTrue();
    expect(visibleNav('superadmin').some((item) => item.path === '/studio')).toBeTrue();
    expect(visibleNav('operator').some((item) => item.path === '/studio')).toBeFalse();
  });
  it('keeps owner protection and role-specific landing', () => {
    const children = routes.find((route) => route.path === '')?.children;
    expect(children?.find((route) => route.path === 'studio')?.canActivate).toContain(ownerGuard);
    expect(children?.find((route) => route.path === '')?.canActivate).toContain(landingGuard);
    expect(defaultLanding('owner')).toBe('/studio');
    expect(defaultLanding('superadmin')).toBe('/studio');
    expect(defaultLanding('operator')).toBe('/inbox');
  });
});
