import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Paperclip, SendHorizontal, X } from 'lucide-react';
import { useRef, useState, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { qk } from '@/lib/query';
import { chatsApi } from './api';

/** Operator's message box: text and/or one file, sent as the persona (author «оператор»). */
export function Composer({ chatId }: { chatId: number }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  const send = useMutation({
    mutationFn: () => (file ? chatsApi.sendFile(chatId, file, text.trim()) : chatsApi.sendText(chatId, text.trim())),
    onSuccess: () => {
      setText('');
      setFile(null);
      void queryClient.invalidateQueries({ queryKey: qk.timeline(chatId) });
      void queryClient.invalidateQueries({ queryKey: qk.chatLists });
    },
  });

  const canSend = (text.trim() !== '' || file !== null) && !send.isPending;
  const submit = (): void => {
    if (canSend) send.mutate();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    // Enter sends, Shift+Enter is a new line (phones keep Enter as a new line via the send button).
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && window.matchMedia?.('(pointer: fine)').matches) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      className="flex flex-none flex-col gap-2 border-t bg-background p-2 sm:p-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {file && (
        <div className="flex w-fit max-w-full items-center gap-2 rounded-md border bg-muted/50 py-1 pr-1 pl-2.5 text-xs">
          <Paperclip className="size-3.5 flex-none text-muted-foreground" />
          <span className="truncate">{file.name}</span>
          <Button variant="ghost" size="icon-sm" className="size-6" onClick={() => setFile(null)} aria-label="Убрать файл">
            <X className="size-3.5" />
          </Button>
        </div>
      )}
      <div className="flex items-end gap-2">
        <input
          ref={picker}
          type="file"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            e.target.value = '';
          }}
        />
        <Button variant="ghost" size="icon" onClick={() => picker.current?.click()} aria-label="Прикрепить файл">
          <Paperclip />
        </Button>
        <label className="min-w-0 flex-1">
          <span className="sr-only">Сообщение от имени персонажа</span>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={file ? 'Подпись к файлу' : 'Написать как оператор…'}
            className="max-h-40 min-h-9 resize-none"
          />
        </label>
        <Button type="submit" size="icon" disabled={!canSend} aria-label="Отправить">
          <SendHorizontal />
        </Button>
      </div>
    </form>
  );
}
