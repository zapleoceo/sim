import { useMutation } from '@tanstack/react-query';
import { Loader2, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { personaApi } from './api';
import { olderThanDays } from './persona-form-logic';

/**
 * «Удалить старые записи» of calls: one question with two answers — older than her saved keeping
 * period (`days: null`) or every recording (`days: 0`). Transcripts stay (docs/calls.md).
 */
export function CallRecordingsPurge({ personaId, days }: { personaId: number; days: number }) {
  const [open, setOpen] = useState(false);
  const purge = useMutation({
    mutationFn: (keep: number | null) => personaApi.purgeCallRecordings(personaId, keep),
    onSuccess: ({ deleted }) => {
      toast.success(`Удалено записей: ${deleted}`);
      setOpen(false);
    },
  });

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Trash2 />
        Удалить старые записи
      </Button>
      <AlertDialog open={open} onOpenChange={(v) => !purge.isPending && setOpen(v)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить записи звонков?</AlertDialogTitle>
            <AlertDialogDescription>
              Файлы записей удаляются насовсем, расшифровки звонков остаются в ленте.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={purge.isPending}>Отмена</AlertDialogCancel>
            {/* Plain buttons, not Action: the dialog stays open until the request is done. */}
            <Button variant="outline" disabled={purge.isPending} onClick={() => purge.mutate(null)}>
              {purge.isPending && purge.variables === null && <Loader2 className="animate-spin" />}
              {olderThanDays(days)}
            </Button>
            <Button variant="destructive" disabled={purge.isPending} onClick={() => purge.mutate(0)}>
              {purge.isPending && purge.variables === 0 && <Loader2 className="animate-spin" />}
              Все записи
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
