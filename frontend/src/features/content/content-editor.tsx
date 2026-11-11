import { useMutation } from '@tanstack/react-query';
import { Sparkles, ThumbsUp, X } from 'lucide-react';
import { Controller, useForm } from 'react-hook-form';
import { useConfirm } from '@/components/confirm';
import { Field, FormGrid } from '@/components/field';
import { OptionSelect } from '@/components/option-select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { contentApi } from '@/lib/api/content';
import { humanizeChannelError } from '@/lib/channel-error';
import type { Channel, ContentKind, ContentPost, Persona } from '@/lib/models';
import {
  CAPTION_MAX, CONTENT_KINDS, KIND_LABELS, contentFormDefaults, contentFromForm, mediaUrlsValid, statusLabel,
  type ContentFormValues,
} from './content-logic';

/**
 * Post editor. Mounted per selection (keyed by the parent), so saving does not reset the form.
 * `post` is null for a new post until the first save.
 */
export function ContentEditor({
  post,
  personas,
  igChannels,
  canApprove,
  onSaved,
  onDeleted,
  onClose,
}: {
  post: ContentPost | null;
  personas: Persona[];
  igChannels: Channel[];
  canApprove: boolean;
  /** `quiet`: no «saved» toast (the draft saved before caption generation). */
  onSaved: (post: ContentPost, quiet?: boolean) => void;
  onDeleted: (id: number) => void;
  onClose: () => void;
}) {
  const confirm = useConfirm();
  const form = useForm<ContentFormValues>({ defaultValues: contentFormDefaults(post, personas) });
  const { register, control, handleSubmit, formState, watch, setValue } = form;
  const caption = watch('caption');

  const persist = (values: ContentFormValues): Promise<ContentPost> => {
    const body = contentFromForm(values);
    return post ? contentApi.update(post.id, body) : contentApi.create(body);
  };

  const save = useMutation({ mutationFn: persist, onSuccess: (p) => { onSaved(p); } });

  // Captions are generated for a saved post: a new one is saved as a draft first.
  const generate = useMutation({
    mutationFn: async (values: ContentFormValues) => {
      const saved = await persist(values);
      onSaved(saved, true);
      const { caption: text } = await contentApi.generateCaption(saved.id);
      return text;
    },
    onSuccess: (text) => setValue('caption', text, { shouldDirty: true }),
  });

  const approve = useMutation({
    mutationFn: (id: number) => contentApi.approve(id),
    onSuccess: (p) => onSaved(p),
  });

  const remove = useMutation({
    mutationFn: (id: number) => contentApi.remove(id),
    onSuccess: (_r, id) => onDeleted(id),
  });

  const busy = save.isPending || generate.isPending || approve.isPending || remove.isPending;
  const error = humanizeChannelError(post?.error);

  const askRemove = async (): Promise<void> => {
    if (!post) return;
    const ok = await confirm({ title: 'Удалить пост?', message: 'Пост будет удалён из плана публикаций.', confirmLabel: 'Удалить', danger: true });
    if (ok) remove.mutate(post.id);
  };

  return (
    <form
      noValidate
      onSubmit={handleSubmit((v) => save.mutate(v))}
      aria-label={post ? 'Редактирование поста' : 'Новый пост'}
      className="flex flex-col gap-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{post ? 'Редактирование поста' : 'Новый пост'}</h2>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Закрыть редактор">
          <X />
        </Button>
      </div>

      <FormGrid>
        <Controller
          control={control}
          name="persona_id"
          rules={{ validate: (v) => v !== null || 'Выберите персонажа' }}
          render={({ field, fieldState }) => (
            <Field label="Персонаж" error={fieldState.error?.message}>
              <OptionSelect
                value={field.value}
                onChange={field.onChange}
                placeholder="Выберите персонажа"
                options={personas.map((p) => ({ value: p.id as number | null, label: p.name }))}
              />
            </Field>
          )}
        />
        <Controller
          control={control}
          name="channel_id"
          render={({ field }) => (
            <Field label="Аккаунт Instagram">
              <OptionSelect
                value={field.value}
                onChange={field.onChange}
                options={[{ value: null, label: 'Не выбран' }, ...igChannels.map((c) => ({ value: c.id, label: c.name }))]}
              />
            </Field>
          )}
        />
        <Controller
          control={control}
          name="kind"
          render={({ field }) => (
            <Field label="Тип">
              <OptionSelect<ContentKind>
                value={field.value}
                onChange={field.onChange}
                options={CONTENT_KINDS.map((k) => ({ value: k, label: KIND_LABELS[k] }))}
              />
            </Field>
          )}
        />
        <Field label="Публикация" hint="Пусто — без расписания">
          <Input type="datetime-local" {...register('scheduled_at')} />
        </Field>

        <Field
          label="Подпись"
          className="col-span-full"
          counter={`${caption.length} / ${CAPTION_MAX}`}
          error={formState.errors.caption?.message}
        >
          <Textarea
            rows={6}
            className="max-h-80"
            {...register('caption', { maxLength: { value: CAPTION_MAX, message: `Не длиннее ${CAPTION_MAX} символов` } })}
          />
        </Field>
        <div className="col-span-full flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={busy} onClick={() => void handleSubmit((v) => generate.mutate(v))()}>
            <Sparkles />
            Сгенерировать подпись
          </Button>
          <span className="text-xs text-muted-foreground">В стиле выбранного персонажа. Новый пост сначала сохранится как черновик.</span>
        </div>

        <Field label="Ссылки на медиа (по одной в строке)" className="col-span-full" error={formState.errors.media?.message}>
          <Textarea
            rows={3}
            placeholder="https://…/photo.jpg"
            {...register('media', { validate: (v) => mediaUrlsValid(v) || 'Каждая строка должна быть ссылкой https://…' })}
          />
        </Field>
      </FormGrid>

      {post && (
        <p className="text-sm text-muted-foreground">
          Статус: {statusLabel(post.status)}
          {error && (
            <>
              {' · '}
              <span className="text-destructive">{error}</span>
            </>
          )}
        </p>
      )}

      <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-2 border-t bg-card px-1 pt-3">
        {post && (
          <Button variant="ghost-destructive" disabled={busy} onClick={() => void askRemove()}>
            Удалить
          </Button>
        )}
        <span className="flex-1" />
        {canApprove && post?.status === 'draft' && (
          <Button variant="outline" disabled={busy} onClick={() => approve.mutate(post.id)}>
            <ThumbsUp />
            Одобрить
          </Button>
        )}
        <Button type="submit" disabled={busy}>
          Сохранить
        </Button>
      </div>
    </form>
  );
}
