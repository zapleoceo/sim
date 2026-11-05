import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { AbstractControl, FormControl, FormGroup, ReactiveFormsModule, ValidationErrors, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { EMPTY, Observable, catchError, finalize, forkJoin, of, switchMap } from 'rxjs';
import { ChannelsApi } from '../../core/api/channels.api';
import { ContentApi } from '../../core/api/content.api';
import { PersonasApi } from '../../core/api/personas.api';
import { AuthService } from '../../core/auth.service';
import { Channel, ContentIn, ContentKind, ContentPost, ContentStatus, Persona } from '../../core/models';
import { ToastService } from '../../core/toast.service';
import { ConfirmService } from '../../shared/confirm-dialog.component';
import { EmptyStateComponent } from '../../shared/empty-state.component';
import { formatDateTime, fromLocalInput, toLocalInput } from '../../shared/format';
import { PageHeaderComponent } from '../../shared/page-header.component';

type Selection = ContentPost | 'new' | null;

const COLUMNS: { status: ContentStatus; label: string; icon: string }[] = [
  { status: 'draft', label: 'Черновики', icon: 'edit_note' },
  { status: 'approved', label: 'Одобрено', icon: 'thumb_up' },
  { status: 'scheduled', label: 'Запланировано', icon: 'schedule' },
  { status: 'published', label: 'Опубликовано', icon: 'check_circle' },
  { status: 'failed', label: 'Ошибка', icon: 'error_outline' },
];

const KIND_LABELS: Record<ContentKind, string> = { photo: 'Фото', video: 'Видео', reel: 'Reels', story: 'Сторис' };

export function parseMediaUrls(text: string): string[] {
  return text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
}

function mediaValidator(c: AbstractControl<string>): ValidationErrors | null {
  const bad = parseMediaUrls(c.value).some((u) => !/^(https?:\/\/|\/)\S+$/i.test(u));
  return bad ? { mediaUrl: true } : null;
}

@Component({
  selector: 'sim-content',
  imports: [
    ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule, MatSelectModule,
    PageHeaderComponent, EmptyStateComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './content.component.html',
  styleUrl: './content.component.scss',
})
export class ContentComponent {
  private readonly api = inject(ContentApi);
  private readonly personasApi = inject(PersonasApi);
  private readonly channelsApi = inject(ChannelsApi);
  private readonly auth = inject(AuthService);
  private readonly toast = inject(ToastService);
  private readonly confirm = inject(ConfirmService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly kinds = (Object.keys(KIND_LABELS) as ContentKind[]).map((id) => ({ id, label: KIND_LABELS[id] }));
  protected readonly isOwner = this.auth.isOwner;

  protected readonly form = new FormGroup({
    persona_id: new FormControl<number | null>(null, [Validators.required]),
    channel_id: new FormControl<number | null>(null),
    kind: new FormControl<ContentKind>('photo', { nonNullable: true }),
    caption: new FormControl('', { nonNullable: true, validators: [Validators.maxLength(2200)] }),
    media: new FormControl('', { nonNullable: true, validators: [mediaValidator] }),
    scheduled_at: new FormControl('', { nonNullable: true }),
  });

  protected readonly posts = signal<ContentPost[]>([]);
  protected readonly personas = signal<Persona[]>([]);
  protected readonly channels = signal<Channel[]>([]);
  protected readonly selected = signal<Selection>(null);
  protected readonly loaded = signal(false);
  protected readonly busy = signal(false);

  protected readonly columns = computed(() =>
    COLUMNS.map((c) => ({
      ...c,
      posts: this.posts()
        .filter((p) => p.status === c.status)
        .map((post) => ({
          post,
          kind: KIND_LABELS[post.kind],
          when: formatDateTime(post.scheduled_at ?? post.published_at),
        })),
    })).filter((c) => c.posts.length > 0 || c.status !== 'failed'),
  );
  protected readonly igChannels = computed(() => this.channels().filter((c) => c.kind === 'instagram'));
  protected readonly current = computed(() => {
    const s = this.selected();
    return s && s !== 'new' ? s : null;
  });
  protected readonly canApprove = computed(() => this.isOwner() && this.current()?.status === 'draft');

  constructor() {
    forkJoin({
      posts: this.api.list(),
      personas: this.personasApi.list().pipe(catchError(() => of([] as Persona[]))),
      channels: this.channelsApi.list().pipe(catchError(() => of([] as Channel[]))),
    })
      .pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef))
      .subscribe((r) => {
        this.posts.set(r.posts);
        this.personas.set(r.personas);
        this.channels.set(r.channels);
        this.loaded.set(true);
      });

    effect(() => {
      const s = this.selected();
      if (s === null) return;
      const p = s === 'new' ? null : s;
      this.form.reset({
        persona_id: p?.persona_id ?? (this.personas()[0]?.id ?? null),
        channel_id: p?.channel_id ?? null,
        kind: p?.kind ?? 'photo',
        caption: p?.caption ?? '',
        media: (p?.media_urls ?? []).join('\n'),
        scheduled_at: toLocalInput(p?.scheduled_at),
      });
    });
  }

  protected save(): void {
    this.persist().pipe(catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef)).subscribe((p) => {
      this.toast.info('Сохранено');
      this.selected.set(p);
    });
  }

  protected generateCaption(): void {
    // Подпись генерируется по сохранённому посту: новый сначала сохраняем как черновик.
    this.persist()
      .pipe(
        switchMap((p) => this.api.generateCaption(p.id).pipe(switchMap((r) => of({ p, caption: r.caption })))),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(({ p, caption }) => {
        this.selected.set(p);
        this.form.controls.caption.setValue(caption);
        this.form.controls.caption.markAsDirty();
      });
  }

  protected approve(): void {
    const p = this.current();
    if (!p) return;
    this.busy.set(true);
    this.api
      .approve(p.id)
      .pipe(
        finalize(() => this.busy.set(false)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((updated) => {
        this.replace(updated);
        this.selected.set(updated);
        this.toast.info('Пост одобрен');
      });
  }

  protected remove(): void {
    const p = this.current();
    if (!p) return;
    this.confirm
      .confirm({ title: 'Удалить пост?', message: 'Пост будет удалён из плана публикаций.', confirmLabel: 'Удалить', danger: true })
      .pipe(
        switchMap(() => this.api.remove(p.id)),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.posts.update((list) => list.filter((x) => x.id !== p.id));
        this.selected.set(null);
      });
  }

  private persist(): Observable<ContentPost> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return EMPTY;
    }
    const v = this.form.getRawValue();
    const body: ContentIn = {
      persona_id: v.persona_id as number,
      channel_id: v.channel_id,
      kind: v.kind,
      caption: v.caption,
      media_urls: parseMediaUrls(v.media),
      scheduled_at: fromLocalInput(v.scheduled_at),
    };
    const p = this.current();
    this.busy.set(true);
    return (p ? this.api.update(p.id, body) : this.api.create(body)).pipe(
      switchMap((saved) => {
        this.replace(saved);
        return of(saved);
      }),
      finalize(() => this.busy.set(false)),
    );
  }

  private replace(p: ContentPost): void {
    this.posts.update((list) => (list.some((x) => x.id === p.id) ? list.map((x) => (x.id === p.id ? p : x)) : [p, ...list]));
  }
}
