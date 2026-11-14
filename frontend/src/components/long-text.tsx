import { Pencil } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { Textarea } from './ui/textarea';

/** Classes of the short preview: at most four lines, long words wrap instead of widening the page. */
export const LONG_TEXT_PREVIEW = 'line-clamp-4 whitespace-pre-wrap [overflow-wrap:anywhere]';

const count = (text: string): string => `${text.length.toLocaleString('ru-RU')} симв.`;

/**
 * A long text (a prompt, a scenario) that does not take half of the screen: a preview of a few lines
 * and «Редактировать», which opens the whole text in a dialog (full screen on a phone). The preview is
 * clamped by CSS only; the value is controlled by the caller and is never cut. Edits go to `onChange`
 * as they are typed, so closing the dialog in any way keeps them.
 */
export function LongText({
  label,
  value,
  onChange,
  onBlur,
  hint,
  error,
  anchorId,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  hint?: ReactNode;
  error?: ReactNode;
  /** `id` of the whole field, so a link elsewhere on the page can point at it. */
  anchorId?: string;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const filled = value.trim() !== '';

  return (
    <div id={anchorId} className="flex min-w-0 scroll-mt-4 flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3">
        <span id={`${id}-label`} className="min-w-0 text-sm leading-tight font-medium">{label}</span>
        <Button variant="outline" size="sm" className="flex-none" aria-label={`Редактировать: ${label}`} onClick={() => setOpen(true)}>
          <Pencil />
          Редактировать
        </Button>
      </div>
      <p
        data-slot="long-text-preview"
        aria-labelledby={`${id}-label`}
        onClick={() => setOpen(true)}
        className={cn(
          LONG_TEXT_PREVIEW,
          'cursor-pointer rounded-md bg-muted/50 px-3 py-2 text-[13px] leading-relaxed',
          !filled && 'text-muted-foreground',
          error && 'ring-1 ring-destructive',
        )}
      >
        {filled ? value : (placeholder ?? 'Пусто')}
      </p>
      <div className="flex items-start justify-between gap-3 text-xs">
        {error ? (
          <p role="alert" className="text-destructive">{error}</p>
        ) : hint ? (
          <p className="text-muted-foreground">{hint}</p>
        ) : (
          <span />
        )}
        {filled && <span className="flex-none text-muted-foreground tabular">{count(value)}</span>}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          {...(hint ? {} : { 'aria-describedby': undefined })}
          className="touch-40 top-4 flex h-[calc(var(--app-vh,100dvh)-2rem)] translate-y-0 flex-col gap-3 p-3 sm:top-[50%] sm:h-[min(48rem,calc(100dvh-4rem))] sm:max-w-3xl sm:translate-y-[-50%] sm:p-5"
        >
          <DialogHeader>
            <DialogTitle className="text-base leading-tight">{label}</DialogTitle>
            {hint && <DialogDescription className="text-xs">{hint}</DialogDescription>}
          </DialogHeader>
          <Textarea
            aria-label={label}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onBlur={onBlur}
            placeholder={placeholder}
            className="min-h-0 flex-1 resize-none md:font-mono md:text-[13px]"
          />
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground tabular">{count(value)}</span>
            <Button onClick={() => setOpen(false)}>Готово</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
