import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ActivatedRoute, Router } from '@angular/router';
import { EMPTY, catchError, finalize } from 'rxjs';
import { AuthService } from '../../core/auth.service';
import { errorMessage } from '../../core/interceptors';
import { HttpErrorResponse } from '@angular/common/http';

@Component({
  selector: 'sim-login',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatButtonModule, MatProgressSpinnerModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form class="sim-card card" [formGroup]="form" (ngSubmit)="submit()">
      <div class="brand"><span class="logo" aria-hidden="true">S</span><h1>Sim</h1></div>
      <p class="sim-muted">Вход в админ-панель</p>

      <mat-form-field appearance="outline">
        <mat-label>Логин</mat-label>
        <input matInput formControlName="username" autocomplete="username" />
        @if (form.controls.username.hasError('required')) {
          <mat-error>Введите логин</mat-error>
        }
      </mat-form-field>

      <mat-form-field appearance="outline">
        <mat-label>Пароль</mat-label>
        <input matInput type="password" formControlName="password" autocomplete="current-password" />
        @if (form.controls.password.hasError('required')) {
          <mat-error>Введите пароль</mat-error>
        }
      </mat-form-field>

      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }

      <button mat-flat-button type="submit" [disabled]="loading()">
        @if (loading()) {
          <mat-spinner diameter="18" />
        } @else {
          Войти
        }
      </button>
    </form>
  `,
  styles: `
    :host { display: grid; place-items: center; min-height: 100%; padding: var(--sim-space-4); }
    .card { width: 100%; max-width: 380px; display: flex; flex-direction: column; gap: var(--sim-space-2); padding: var(--sim-space-6); }
    .brand { display: flex; align-items: center; gap: var(--sim-space-3); }
    .logo { width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; background: var(--sim-accent); color: var(--mat-sys-on-primary); font-weight: 600; font-size: 18px; }
    p.sim-muted { margin-bottom: var(--sim-space-3); }
    .error { color: var(--sim-danger); }
    button { height: 44px; }
  `,
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly form = new FormGroup({
    username: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    password: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });
  protected readonly loading = signal(false);
  protected readonly error = signal('');

  protected submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.loading.set(true);
    this.error.set('');
    const { username, password } = this.form.getRawValue();
    this.auth
      .login(username.trim(), password)
      .pipe(
        finalize(() => this.loading.set(false)),
        catchError((e: unknown) => {
          this.error.set(e instanceof HttpErrorResponse && e.status === 401 ? 'Неверный логин или пароль' : errorMessage(e));
          return EMPTY;
        }),
      )
      .subscribe(() => void this.router.navigateByUrl(this.route.snapshot.queryParamMap.get('returnUrl') ?? '/'));
  }
}
