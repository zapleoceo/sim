import { Injectable, inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

@Injectable({ providedIn: 'root' })
export class ToastService {
  private readonly snack = inject(MatSnackBar);

  info(message: string): void {
    this.snack.open(message, 'OK', { duration: 3500 });
  }

  error(message: string): void {
    this.snack.open(message, 'Закрыть', { duration: 6000, panelClass: 'sim-toast-error' });
  }
}
