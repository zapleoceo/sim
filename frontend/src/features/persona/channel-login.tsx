import { useQueryClient } from '@tanstack/react-query';
import { Loader2, X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { InlineError } from '@/components/empty-state';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { qk } from '@/lib/query';
import type { LoginResponse } from '@/lib/types';
import { channelApi } from './api';
import { inProgress, shouldPoll } from './channel-logic';
import { usePersonaId } from './persona-scope';

export const LOGIN_POLL_MS = 2_000;

/** Props of every «Подключить …» form (one per messenger, sim/domain/channels.py). */
export interface ConnectProps {
  onClose: () => void;
  pollMs?: number;
}

/**
 * The steps of connecting one account, the same for every messenger: the current step from the
 * server, re-read every `pollMs` while a QR may be scanned or the Instagram app approval is
 * awaited, and «connected» → toast, lists re-read, form closed. Secrets are only sent.
 */
export function useChannelLogin({ onClose, pollMs = LOGIN_POLL_MS }: ConnectProps) {
  const personaId = usePersonaId();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<LoginResponse | null>(null);
  const reported = useRef(false);
  const state = step?.login.state;
  const channelId = step?.channel.id;

  useEffect(() => {
    if (!shouldPoll(state) || channelId === undefined) return undefined;
    let alive = true;
    const timer = window.setTimeout(() => {
      channelApi
        .loginState(personaId, channelId)
        .then((next) => {
          if (alive) setStep(next);
        })
        .catch((error: unknown) => {
          if (alive) setStep((prev) => prev && { ...prev, login: { state: 'error', message: errorMessage(error), qr: null } });
        });
    }, pollMs);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [state, step, channelId, personaId, pollMs]);

  useEffect(() => {
    if (state !== 'connected' || reported.current) return;
    reported.current = true;
    toast.success(`Аккаунт ${step?.channel.account_label ?? ''} подключён. Персонаж выйдет в сеть в течение минуты`);
    void queryClient.invalidateQueries({ queryKey: qk.channels(personaId) });
    void queryClient.invalidateQueries({ queryKey: qk.personaList });
    onClose();
  }, [state, step, queryClient, personaId, onClose]);

  const cancel = (): void => {
    if (channelId !== undefined && inProgress(state)) {
      void channelApi
        .cancel(personaId, channelId)
        .catch(() => undefined)
        .finally(() => void queryClient.invalidateQueries({ queryKey: qk.channels(personaId) }));
    }
    onClose();
  };

  return { personaId, step, setStep, state, channelId, cancel };
}

/** The frame of a connect form: its steps, an error with «Попробовать снова», «Отменить подключение». */
export function LoginShell({
  label,
  step,
  onRetry,
  onCancel,
  children,
}: {
  label: string;
  step: LoginResponse | null;
  onRetry: () => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  const state = step?.login.state;
  return (
    <div className="flex flex-col gap-4 rounded-lg border p-3 sm:p-4" role="region" aria-label={label}>
      {children}
      {(state === 'error' || state === 'none') && step && (
        <div className="flex flex-col gap-3">
          <InlineError>{step.login.message}</InlineError>
          <div className="flex justify-end">
            <Button variant="outline" onClick={onRetry}>Попробовать снова</Button>
          </div>
        </div>
      )}
      {step !== null && state !== 'connected' && (
        <div className="flex justify-end border-t pt-3">
          <Button variant="ghost" size="sm" onClick={onCancel}>
            <X />
            Отменить подключение
          </Button>
        </div>
      )}
    </div>
  );
}

/** A QR code to scan in the messenger's app, re-read by the hook until it is scanned. */
export function QrStep({ step, alt }: { step: LoginResponse; alt: string }) {
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      {step.login.qr && <img src={step.login.qr} alt={alt} className="size-56 rounded-md bg-white p-2 sm:size-64" />}
      <p className="max-w-md text-sm text-muted-foreground">{step.login.message}</p>
      <p className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
        <Loader2 className="size-3 animate-spin" />
        Жду, когда отсканируете…
      </p>
    </div>
  );
}
