import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTableModule } from '@angular/material/table';
import { EMPTY, catchError, finalize, switchMap } from 'rxjs';
import { AdminsApi } from '../../core/api/admins.api';
import { AuthService } from '../../core/auth.service';
import { AdminCreate, AdminUser, Role } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { AvatarComponent } from '../../shared/avatar.component';
import { ConfirmService } from '../../shared/confirm-dialog.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { ROLE_LABELS } from '../../shell/nav';

const ROLES: readonly Role[] = ['superadmin', 'owner', 'operator'];

/** «123456» -> telegram_id, «@name» / «name» -> tg_username. */
export function parseAdminTarget(raw: string): Pick<AdminCreate, 'telegram_id' | 'tg_username'> {
  const value = raw.trim();
  return /^\d+$/.test(value) ? { telegram_id: Number(value) } : { tg_username: value.replace(/^@/, '') };
}

@Component({
  selector: 'sim-admins',
  imports: [
    ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule, MatSelectModule, MatTableModule,
    PageHeaderComponent, EmptyStateComponent, AvatarComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './admins.component.html',
  styleUrl: './admins.component.scss',
})
export class AdminsComponent {
  private readonly api = inject(AdminsApi);
  private readonly auth = inject(AuthService);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly roles = ROLES;
  protected readonly columns = ['admin', 'username', 'telegram_id', 'role', 'status', 'actions'];
  protected readonly roleLabels = ROLE_LABELS;
  protected readonly meId = this.auth.user()?.id ?? null;

  protected readonly form = new FormGroup({
    target: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^(\d+|@?[A-Za-z0-9_]{3,32})$/)] }),
    role: new FormControl<Role>('operator', { nonNullable: true }),
  });

  protected readonly admins = signal<AdminUser[]>([]);
  protected readonly loaded = signal(false);
  protected readonly saving = signal(false);

  constructor() {
    this.api
      .list()
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((items) => {
        this.admins.set(items);
        this.loaded.set(true);
      });
  }

  protected name(a: AdminUser): string {
    return a.display_name || a.tg_username || String(a.telegram_id ?? '');
  }

  protected add(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { target, role } = this.form.getRawValue();
    this.saving.set(true);
    this.api
      .create({ ...parseAdminTarget(target), role })
      .pipe(
        finalize(() => this.saving.set(false)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((created) => {
        this.admins.update((list) => [...list, created]);
        this.form.controls.target.reset('');
        this.toast.info('Администратор добавлен');
      });
  }

  protected changeRole(a: AdminUser, role: Role): void {
    if (role === a.role) return;
    this.patch(a, { role });
  }

  protected toggleActive(a: AdminUser): void {
    this.patch(a, { is_active: !a.is_active });
  }

  protected remove(a: AdminUser): void {
    this.confirm
      .confirm({
        title: 'Удалить администратора?',
        message: `${this.name(a)} потеряет доступ к админке.`,
        confirmLabel: 'Удалить',
        danger: true,
      })
      .pipe(
        switchMap(() => this.api.remove(a.id)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.admins.update((list) => list.filter((x) => x.id !== a.id));
        this.toast.info('Администратор удалён');
      });
  }

  private patch(a: AdminUser, body: { role?: Role; is_active?: boolean }): void {
    this.api
      .update(a.id, body)
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((updated) => this.admins.update((list) => list.map((x) => (x.id === updated.id ? updated : x))));
  }
}
