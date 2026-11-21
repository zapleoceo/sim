import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Copy, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Field } from '@/components/field';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { qk } from '@/lib/query';
import type { ClonePart, Persona } from '@/lib/types';
import { personaApi } from './api';
import { CLONE_PARTS, cloneNameError, defaultParts, orderedParts } from './clone-logic';

export interface CloneDialogProps {
  source: { id: number; name: string };
  /** Names already taken (checked before the request; the server checks again). */
  takenNames?: readonly string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloned: (persona: Persona) => void;
}

/** «Клонировать»: a new persona from this one, with the ticked parts only. */
export function CloneDialog({ source, takenNames = [], open, onOpenChange, onCloned }: CloneDialogProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [parts, setParts] = useState<Set<ClonePart>>(defaultParts);
  const [error, setError] = useState<string | null>(null);
  const clone = useMutation({
    mutationFn: () => personaApi.clone(source.id, name.trim().replace(/\s+/g, ' '), orderedParts(parts)),
    onSuccess: (persona) => {
      toast.success(`Персонаж «${persona.name}» создан. Подключите ему Telegram в разделе «Канал»`);
      void queryClient.invalidateQueries({ queryKey: qk.personas });
      setName('');
      setParts(defaultParts());
      onOpenChange(false);
      onCloned(persona);
    },
  });
  const toggle = (part: ClonePart): void =>
    setParts((prev) => {
      const next = new Set(prev);
      if (next.has(part)) next.delete(part);
      else next.add(part);
      return next;
    });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Клонировать «{source.name}»</DialogTitle>
          <DialogDescription>
            Новый персонаж получит только отмеченное. Канал, клиенты, чаты, дни и сгенерированные медиа не копируются.
          </DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            const problem = cloneNameError(name, takenNames);
            setError(problem);
            if (!problem) clone.mutate();
          }}
        >
          <Field id="clone-name" label="Имя нового персонажа" error={error ?? undefined}>
            <Input id="clone-name" autoComplete="off" value={name} aria-invalid={!!error} onChange={(e) => setName(e.target.value)} />
          </Field>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-sm font-medium">Что скопировать</legend>
            {CLONE_PARTS.map((p) => (
              <label key={p.part} className="flex cursor-pointer items-start gap-3 rounded-md px-1 py-1.5 hover:bg-accent/60">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 flex-none accent-primary"
                  checked={parts.has(p.part)}
                  onChange={() => toggle(p.part)}
                />
                <span className="flex min-w-0 flex-col">
                  <span className="text-sm">{p.label}</span>
                  <span className="text-xs text-muted-foreground">{p.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
            <Button type="submit" disabled={clone.isPending}>
              {clone.isPending ? <Loader2 className="animate-spin" /> : <Copy />}
              Клонировать
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
