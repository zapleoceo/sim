import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { catchError, debounceTime, of } from 'rxjs';
import { LlmActivityFilter, MessageActivityFilter } from '../../core/activity.models';
import { Author, Channel, ChannelKind, Delivery, Direction, Persona } from '../../core/models';
import { ChannelsApi } from '../../core/api/channels.api';
import { PersonasApi } from '../../core/api/personas.api';
import { CHANNEL_KINDS, CHANNEL_LABELS } from '../../core/stages';
import { FilterBarComponent, FiltersTemplateDirective } from '../../shared/filter-bar.component';
import { DELIVERY_LABELS } from '../../shared/message-card.component';

export type ActivityFilters = MessageActivityFilter & LlmActivityFilter;
export function createActivityForm() {
  return new FormGroup({
    q: new FormControl('', { nonNullable: true, validators: Validators.maxLength(200) }),
    channel_kind: new FormControl<ChannelKind | ''>('', { nonNullable: true }),
    channel_id: new FormControl<number | null>(null, [Validators.min(1), Validators.pattern(/^\d+$/)]),
    persona_id: new FormControl<number | null>(null, [Validators.min(1), Validators.pattern(/^\d+$/)]),
    client_id: new FormControl<number | null>(null, [Validators.min(1), Validators.pattern(/^\d+$/)]),
    direction: new FormControl<Direction | ''>('', { nonNullable: true }),
    author: new FormControl<Author | ''>('', { nonNullable: true }),
    delivery: new FormControl<Delivery | ''>('', { nonNullable: true }),
    from: new FormControl('', { nonNullable: true }), to: new FormControl('', { nonNullable: true }),
    workflow: new FormControl('', { nonNullable: true, validators: Validators.maxLength(32) }),
    model: new FormControl('', { nonNullable: true, validators: Validators.maxLength(120) }),
    provider: new FormControl('', { nonNullable: true, validators: Validators.maxLength(64) }),
    has_error: new FormControl<boolean | null>(null),
  }, { validators: (form) => {
    const from: unknown = form.get('from')?.value;
    const to: unknown = form.get('to')?.value;
    return typeof from === 'string' && typeof to === 'string' && from && to && from > to ? { dateOrder: true } : null;
  } });
}

@Component({
  selector: 'sim-activity-filters',
  imports: [ReactiveFormsModule, MatFormFieldModule, MatInputModule, MatSelectModule, FilterBarComponent, FiltersTemplateDirective],
  templateUrl: './activity-filters.component.html',
  styleUrl: './activity-filters.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ActivityFiltersComponent {
  readonly llm = input(false);
  readonly clientScoped = input(false);
  readonly channelId = input<number | null>(null);
  readonly changed = output<ActivityFilters | null>();
  private readonly channelsApi = inject(ChannelsApi);
  private readonly personasApi = inject(PersonasApi);
  protected readonly channels = signal<Channel[]>([]);
  protected readonly personas = signal<Persona[]>([]);
  protected readonly optionsError = signal(false);
  protected readonly selectedChannelMissing = computed(() => {
    const id = this.values().channel_id;
    return id != null && !this.channels().some((c) => c.id === id) ? id : null;
  });
  protected readonly form = createActivityForm();
  protected readonly kinds = CHANNEL_KINDS;
  protected readonly labels = CHANNEL_LABELS;
  protected readonly deliveries = Object.entries(DELIVERY_LABELS).map(([value, label]) => ({ value, label }));
  private readonly destroyRef = inject(DestroyRef);
  private readonly values = toSignal(this.form.valueChanges, { initialValue: this.form.getRawValue() });
  protected readonly count = computed(() => Object.values(this.values()).filter((v) => v !== '' && v !== null && v !== undefined).length);

  constructor() {
    this.channelsApi.list().pipe(catchError(() => { this.optionsError.set(true); return of([]); }), takeUntilDestroyed(this.destroyRef)).subscribe((items) => this.channels.set(items));
    this.personasApi.list().pipe(catchError(() => { this.optionsError.set(true); return of([]); }), takeUntilDestroyed(this.destroyRef)).subscribe((items) => this.personas.set(items));
    effect(() => {
      this.form.controls.channel_id.setValue(this.channelId());
      this.emit();
    });
    this.form.valueChanges.pipe(debounceTime(250), takeUntilDestroyed(this.destroyRef)).subscribe(() => this.emit());
  }

  protected reset(): void { this.form.reset(); }

  private emit(): void {
    if (this.form.invalid) { this.changed.emit(null); return; }
    const v = this.form.getRawValue();
    const common = { q: v.q.trim(), channel_kind: v.channel_kind, channel_id: v.channel_id,
      persona_id: v.persona_id, client_id: this.clientScoped() ? null : v.client_id, from: v.from, to: v.to };
    this.changed.emit(this.llm()
      ? { ...common, workflow: v.workflow.trim(), model: v.model.trim(), provider: v.provider.trim(), has_error: v.has_error }
      : { ...common, direction: v.direction, author: v.author, delivery: v.delivery });
  }
}
