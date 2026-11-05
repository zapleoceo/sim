import { ChangeDetectionStrategy, Component, DestroyRef, effect, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { EMPTY, catchError, finalize, switchMap } from 'rxjs';
import { PersonasApi } from '../../core/api/personas.api';
import { BrokerModel, Persona, Strategy } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { ConfirmService } from '../../shared/confirm-dialog.component';
import { EMPTY_PERSONA, createPersonaForm, personaToInput } from './persona-form';
import { PersonaPreviewComponent } from './persona-preview.component';

@Component({
  selector: 'sim-persona-editor',
  imports: [
    ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatSlideToggleModule,
    PersonaPreviewComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './persona-editor.component.html',
  styleUrl: './persona-editor.component.scss',
})
export class PersonaEditorComponent {
  /** `null` — создание нового персонажа. */
  readonly persona = input<Persona | null>(null);
  readonly models = input<BrokerModel[]>([]);
  readonly strategies = input<Strategy[]>([]);

  readonly saved = output<Persona>();
  readonly removed = output<number>();
  readonly closed = output<void>();

  private readonly api = inject(PersonasApi);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly form = createPersonaForm();
  protected readonly saving = signal(false);

  constructor() {
    effect(() => {
      const p = this.persona();
      this.form.reset(p ? personaToInput(p) : EMPTY_PERSONA);
    });
  }

  protected save(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const body = this.form.getRawValue();
    const p = this.persona();
    this.saving.set(true);
    (p ? this.api.update(p.id, body) : this.api.create(body))
      .pipe(
        finalize(() => this.saving.set(false)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((saved) => {
        this.toast.info('Персонаж сохранён');
        this.saved.emit(saved);
      });
  }

  protected remove(): void {
    const p = this.persona();
    if (!p) return;
    this.confirm
      .confirm({
        title: 'Удалить персонажа?',
        message: `«${p.name}» будет удалён. Диалоги и каналы, привязанные к нему, могут перестать работать.`,
        confirmLabel: 'Удалить',
        danger: true,
      })
      .pipe(
        switchMap(() => this.api.remove(p.id)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => this.removed.emit(p.id));
  }
}
