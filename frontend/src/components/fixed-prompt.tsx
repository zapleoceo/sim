import { Lock } from 'lucide-react';

/**
 * A text that comes from the code and is not editable, shown read-only next to the editable ones:
 * the decision consistency contract (`where`), or any other fixed text under its own `title`.
 */
export function FixedPrompt({ text, where, title }: { text: string; where?: string; title?: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-dashed bg-muted/40 p-3 text-xs">
      <span className="flex items-center gap-1.5 font-medium text-muted-foreground">
        <Lock className="size-3.5" />
        {title ?? `Добавляется ${where ?? ''} и не редактируется`}
      </span>
      <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{text}</p>
    </div>
  );
}
