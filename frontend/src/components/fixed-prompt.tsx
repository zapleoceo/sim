import { Lock } from 'lucide-react';

/** The one non-editable text of a prompt (the honesty rule), shown read-only next to the editable one. */
export function FixedPrompt({ text, where }: { text: string; where: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-dashed bg-muted/40 p-3 text-xs">
      <span className="flex items-center gap-1.5 font-medium text-muted-foreground">
        <Lock className="size-3.5" />
        Добавляется {where} и не редактируется
      </span>
      <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{text}</p>
    </div>
  );
}
