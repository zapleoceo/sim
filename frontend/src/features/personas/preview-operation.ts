import { pollQueued } from '@/lib/api/queued-operation';
export { PreviewOperationError, TerminalPreviewError } from '@/lib/api/queued-operation';
import type { PreviewContext, PreviewResponse, PreviewStageOptions, PreviewTurn } from '@/lib/models';

export type PreviewInput = PreviewContext & {
  message: string;
  history: PreviewTurn[];
  voice_input: boolean;
  stages: PreviewStageOptions;
};

export type CompletedPreview = {
  status: 'complete'; result: PreviewResponse; history: PreviewTurn[]; message: string;
};

export function newPreviewKey(): string {
  return `${Date.now()}-${crypto.randomUUID()}`;
}

function operationPath(persona: number, key: string): string {
  return `/api/personas/${persona}/preview-operations/${encodeURIComponent(key)}`;
}

function operationStorage(persona: number, owner: number): string {
  return `sim.preview.pending.${owner}.${persona}`;
}

export function pendingPreview(persona: number, owner = 0): string | null {
  return sessionStorage.getItem(operationStorage(persona, owner));
}

export function rememberPreview(persona: number, key: string, owner = 0): void {
  sessionStorage.setItem(operationStorage(persona, owner), key);
}

export function forgetPreview(persona: number, key: string, owner = 0): void {
  if (pendingPreview(persona, owner) === key) sessionStorage.removeItem(operationStorage(persona, owner));
}

/** Only a new operation submits input. Recovery starts with GET and never replays POST. */
export async function pollPreview(
  persona: number, key: string, signal: AbortSignal, input?: PreviewInput,
): Promise<CompletedPreview> {
  return pollQueued<CompletedPreview>(operationPath(persona, key), signal, input);
}
