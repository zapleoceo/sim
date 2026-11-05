import { ChangeDetectionStrategy, Component, Injectable, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule } from '@angular/material/dialog';
import { Observable, filter, map } from 'rxjs';

export interface ConfirmData {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
}

@Component({
  selector: 'sim-confirm-dialog',
  imports: [MatDialogModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .danger { --mdc-filled-button-container-color: var(--sim-danger); --mdc-filled-button-label-text-color: #fff; }
  `,
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>{{ data.message }}</mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Отмена</button>
      <button mat-flat-button [mat-dialog-close]="true" cdkFocusInitial [class.danger]="data.danger">{{ data.confirmLabel ?? 'Подтвердить' }}</button>
    </mat-dialog-actions>
  `,
})
export class ConfirmDialogComponent {
  protected readonly data = inject<ConfirmData>(MAT_DIALOG_DATA);
}

@Injectable({ providedIn: 'root' })
export class ConfirmService {
  private readonly dialog = inject(MatDialog);

  /** Эмитит один раз, только если пользователь подтвердил. */
  confirm(data: ConfirmData): Observable<true> {
    return this.dialog
      .open<ConfirmDialogComponent, ConfirmData, boolean>(ConfirmDialogComponent, { data, width: '420px', maxWidth: '92vw' })
      .afterClosed()
      .pipe(
        filter((v) => v === true),
        map(() => true as const),
      );
  }
}
