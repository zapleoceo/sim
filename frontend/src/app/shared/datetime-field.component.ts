import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatDatepickerModule } from '@angular/material/datepicker';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatTimepickerModule } from '@angular/material/timepicker';

/**
 * Дата и время одним значением `Date | null`: MatDatepicker + MatTimepicker на общем контроле
 * (локаль и формат — из DateAdapter, т.е. ru-RU, 24 часа).
 */
@Component({
  selector: 'sim-datetime-field',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatDatepickerModule, MatTimepickerModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <mat-form-field appearance="outline" class="date">
      <mat-label>{{ label() }}</mat-label>
      <input matInput [matDatepicker]="dp" [formControl]="control()" placeholder="дд.мм.гггг" />
      <mat-datepicker-toggle matIconSuffix [for]="dp" />
      <mat-datepicker #dp />
      @if (hint()) {
        <mat-hint>{{ hint() }}</mat-hint>
      }
    </mat-form-field>
    <mat-form-field appearance="outline" class="time">
      <mat-label>Время</mat-label>
      <input matInput [matTimepicker]="tp" [formControl]="control()" placeholder="чч:мм" />
      <mat-timepicker-toggle matIconSuffix [for]="tp" />
      <mat-timepicker #tp interval="15min" />
    </mat-form-field>
  `,
  styles: `
    :host { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 0 var(--sim-space-3); }
    @media (max-width: 400px) { :host { grid-template-columns: minmax(0, 1fr); } }
  `,
})
export class DatetimeFieldComponent {
  readonly control = input.required<FormControl<Date | null>>();
  readonly label = input('Дата');
  readonly hint = input('');
}
