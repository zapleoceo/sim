import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { EMPTY, Observable, catchError, finalize, switchMap, takeWhile, timer } from 'rxjs';
import { ChannelsApi } from '../../core/api/channels.api';
import { Channel, ChannelKind, ConnectState, Persona } from '../../core/models';
import { CHANNEL_KINDS, CHANNEL_LABELS } from '../../core/stages';
import { ChannelIconComponent } from '../../shared/channel-icon.component';

export interface WizardData {
  personas: Persona[];
  /** Если задан — мастер открывается сразу на шаге подключения. */
  channel?: Channel;
}

type Step = 'setup' | 'connect' | 'qr' | 'code' | 'password' | 'done';

const QR_POLL_MS = 3000;

@Component({
  selector: 'sim-channel-wizard',
  imports: [
    ReactiveFormsModule, MatDialogModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule,
    MatProgressSpinnerModule, MatSelectModule, ChannelIconComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './channel-wizard.component.html',
  styleUrl: './channel-wizard.component.scss',
})
export class ChannelWizardComponent {
  protected readonly data = inject<WizardData>(MAT_DIALOG_DATA);

  private readonly api = inject(ChannelsApi);
  private readonly ref = inject<MatDialogRef<ChannelWizardComponent, boolean>>(MatDialogRef);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly kinds = CHANNEL_KINDS;
  protected readonly labels = CHANNEL_LABELS;

  protected readonly setupForm = new FormGroup({
    kind: new FormControl<ChannelKind>('telegram', { nonNullable: true }),
    persona_id: new FormControl<number | null>(null, [Validators.required]),
    name: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(80)] }),
  });
  protected readonly tokenForm = new FormGroup({
    bot_token: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^\d+:[\w-]{20,}$/)] }),
  });
  protected readonly igForm = new FormGroup({
    username: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    password: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    proxy: new FormControl('', { nonNullable: true, validators: [Validators.pattern(/^(\w+:\/\/)?\S+:\d+.*$|^$/)] }),
  });
  protected readonly tguForm = new FormGroup({
    api_id: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^\d+$/)] }),
    api_hash: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^[0-9a-fA-F]{32}$/)] }),
    phone: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^\+?[\d\s()-]{7,20}$/)] }),
    proxy: new FormControl('', { nonNullable: true, validators: [Validators.pattern(/^(\w+:\/\/)?\S+:\d+.*$|^$/)] }),
  });
  protected readonly codeForm = new FormGroup({
    code: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.minLength(4)] }),
  });
  protected readonly passwordForm = new FormGroup({
    password: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });

  protected readonly step = signal<Step>('setup');
  protected readonly channel = signal<Channel | null>(null);
  protected readonly busy = signal(false);
  protected readonly qr = signal<string | null>(null);
  protected readonly message = signal('');
  protected readonly need = signal<'code' | '2fa' | 'challenge' | null>(null);

  protected readonly kind = computed(() => this.channel()?.kind ?? this.setupForm.controls.kind.value);
  protected readonly isNew = !this.data.channel;

  constructor() {
    const existing = this.data.channel;
    if (existing) {
      this.channel.set(existing);
      this.step.set('connect');
    }
    if (this.data.personas.length === 1) {
      this.setupForm.controls.persona_id.setValue(this.data.personas[0]?.id ?? null);
    }
  }

  protected pickKind(kind: ChannelKind): void {
    this.setupForm.controls.kind.setValue(kind);
    if (!this.setupForm.controls.name.value) {
      this.setupForm.controls.name.setValue(CHANNEL_LABELS[kind]);
    }
  }

  protected create(): void {
    if (this.setupForm.invalid) {
      this.setupForm.markAllAsTouched();
      return;
    }
    const v = this.setupForm.getRawValue();
    this.run(this.api.create({ kind: v.kind, persona_id: v.persona_id as number, name: v.name.trim() }), (ch) => {
      this.channel.set(ch);
      this.step.set('connect');
      // WhatsApp не требует ввода — сразу просим QR.
      if (ch.kind === 'whatsapp') this.connect();
    });
  }

  protected connect(): void {
    const ch = this.channel();
    if (!ch) return;
    let body = {};
    if (ch.kind === 'telegram') {
      if (this.tokenForm.invalid) return this.tokenForm.markAllAsTouched();
      body = { bot_token: this.tokenForm.controls.bot_token.value.trim() };
    } else if (ch.kind === 'telegram_user') {
      if (this.tguForm.invalid) return this.tguForm.markAllAsTouched();
      const v = this.tguForm.getRawValue();
      // api_id уходит строкой: бэкенд принимает credentials как dict[str, str].
      body = {
        api_id: v.api_id.trim(), api_hash: v.api_hash.trim(), phone: v.phone.trim(),
        ...(v.proxy.trim() ? { proxy: v.proxy.trim() } : {}),
      };
    } else if (ch.kind === 'instagram') {
      if (this.igForm.invalid) return this.igForm.markAllAsTouched();
      const v = this.igForm.getRawValue();
      body = { username: v.username.trim(), password: v.password, ...(v.proxy.trim() ? { proxy: v.proxy.trim() } : {}) };
    }
    this.run(this.api.connect(ch.id, body), (s) => this.applyState(s));
  }

  protected verify(): void {
    const ch = this.channel();
    if (!ch) return;
    // Шаг пароля 2FA использует тот же verify: значение уходит в поле `code`, пробелы не режем.
    const password = this.step() === 'password';
    const form = password ? this.passwordForm : this.codeForm;
    if (form.invalid) return form.markAllAsTouched();
    const value = password ? this.passwordForm.controls.password.value : this.codeForm.controls.code.value.trim();
    this.run(this.api.verify(ch.id, value), (s) => {
      form.reset();
      this.applyState(s);
    });
  }

  protected finish(): void {
    this.ref.close(true);
  }

  protected cancel(): void {
    // Канал мог быть уже создан — сообщаем родителю, чтобы он обновил список.
    this.ref.close(this.channel() !== null);
  }

  private applyState(s: ConnectState): void {
    this.message.set(s.message ?? '');
    this.need.set(s.need ?? null);
    if (s.status === 'active') {
      this.step.set('done');
      return;
    }
    if (s.need) {
      this.step.set(s.need === '2fa' && this.kind() === 'telegram_user' ? 'password' : 'code');
      return;
    }
    if (s.qr_data_uri || (this.kind() === 'whatsapp' && s.status === 'pending')) {
      this.qr.set(s.qr_data_uri ?? null);
      if (this.step() !== 'qr') {
        this.step.set('qr');
        this.pollState();
      }
      return;
    }
    if (s.status === 'error' || s.status === 'expired') {
      this.step.set('connect');
    }
  }

  /** WhatsApp: QR живёт недолго, поэтому каждые 3 с обновляем состояние, пока канал не станет активным. */
  private pollState(): void {
    const ch = this.channel();
    if (!ch) return;
    timer(QR_POLL_MS, QR_POLL_MS)
      .pipe(
        switchMap(() => this.api.state(ch.id).pipe(catchError(() => EMPTY))),
        takeWhile((s) => s.status !== 'active' && s.status !== 'error' && s.status !== 'expired', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((s) => {
        this.message.set(s.message ?? '');
        if (s.status === 'active') this.step.set('done');
        else if (s.status === 'error' || s.status === 'expired') this.step.set('connect');
        else if (s.qr_data_uri) this.qr.set(s.qr_data_uri);
      });
  }

  private run<T>(source: Observable<T>, next: (v: T) => void): void {
    this.busy.set(true);
    source
      .pipe(
        finalize(() => this.busy.set(false)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(next);
  }
}
