import { Pencil } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { Textarea } from './ui/textarea';

/** Compact editor: wrap long words, retain the complete value. */
export const LONG_TEXT_PREVIEW = 'whitespace-pre-wrap [overflow-wrap:anywhere]';

const count = (text: string): string => `${text.length.toLocaleString('ru-RU')} симв.`;

/** Compact editable text and an optional full editor share the complete value. The parent form saves. */
export function LongText({
  label,
  value,
  onChange,
  onBlur,
  hint,
  error,
  anchorId,
  placeholder,
  disabled = false,
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
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const filled = value.trim() !== '';

  return (
    <div id={anchorId} className="flex min-w-0 scroll-mt-4 flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3">
        <span id={`${id}-label`} className="min-w-0 text-sm leading-tight font-medium">{label}</span>
        <Button disabled={disabled} variant="outline" size="sm" className="flex-none" aria-label={`Редактировать: ${label}`} onClick={() => setOpen(true)}>
          <Pencil />
          Редактировать
        </Button>
      </div>
      <Textarea
        data-slot="long-text-preview"
        aria-labelledby={`${id}-label`}
        aria-invalid={error ? true : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        placeholder={placeholder ?? 'Пусто'}
        disabled={disabled}
        rows={4}
        className={cn(LONG_TEXT_PREVIEW, 'field-sizing-fixed h-28 min-h-28 resize-y text-[13px] leading-relaxed', error && 'ring-1 ring-destructive')}
      />
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
            disabled={disabled}
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
