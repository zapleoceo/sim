import { ApiError, http } from '@/lib/api/http';
import type { PreviewContext, PreviewResponse, PreviewStageOptions, PreviewTurn } from '@/lib/models';

export type PreviewInput = PreviewContext & {
  message: string;
  history: PreviewTurn[];
  voice_input: boolean;
  stages: PreviewStageOptions;
};

type PreviewOperation =
  | { status: 'pending' | 'running' | 'unknown' | 'expired' | 'unavailable' }
  | { status: 'complete'; result: PreviewResponse; history: PreviewTurn[]; message: string };

export type CompletedPreview = Extract<PreviewOperation, { status: 'complete' }>;

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

function wait(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', cancel);
      resolve();
    }, 1500);
    function cancel() {
      window.clearTimeout(timer);
      reject(signal.reason);
    }
    signal.addEventListener('abort', cancel, { once: true });
  });
}

export class PreviewOperationError extends Error {}
export class TerminalPreviewError extends PreviewOperationError {}

/** Only a new operation submits input. Recovery starts with GET and never replays POST. */
export async function pollPreview(
  persona: number, key: string, signal: AbortSignal, input?: PreviewInput,
): Promise<CompletedPreview> {
  signal.throwIfAborted();
  const controller = new AbortController();
  const abort = () => controller.abort(signal.reason);
  signal.addEventListener('abort', abort, { once: true });
  const timeoutError = new PreviewOperationError('Ответ ещё не получен. Можно снова проверить тот же запрос.');
  const timer = window.setTimeout(() => controller.abort(timeoutError), 5 * 60_000);
  try {
    return await pollOperation(operationPath(persona, key), controller.signal, input);
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}

async function pollOperation(
  path: string, requestSignal: AbortSignal, input?: PreviewInput,
): Promise<CompletedPreview> {
  const options = { silent: true, signal: requestSignal };
  let rejection: string | undefined;
  if (input) {
    try {
      await http.post(path, input, options);
    } catch (error) {
      requestSignal.throwIfAborted();
      if (!(error instanceof ApiError)) throw error;
      if (error.status === 429) rejection = 'Очередь заполнена. Дождитесь завершения активного запроса.';
      else if ([404, 409, 422].includes(error.status)) rejection = 'Сервер не принял новый запрос.';
      else if (error.status !== 0 && error.status < 500) throw error;
      // The origin may have committed despite a lost response. Consult the same ID.
    }
  }
  const acceptanceDeadline = Date.now() + 15_000;
  while (!requestSignal.aborted) {
    let operation: PreviewOperation;
    try {
      operation = await http.get<PreviewOperation>(path, options);
    } catch (error) {
      requestSignal.throwIfAborted();
      if (error instanceof ApiError && error.status === 404) {
        if (rejection) throw new PreviewOperationError(rejection);
        if (Date.now() >= acceptanceDeadline)
          throw new PreviewOperationError('Постановка запроса не подтверждена. Можно проверить его снова.');
        await wait(requestSignal);
        continue;
      }
      if (!(error instanceof ApiError) || (error.status !== 0 && error.status < 500)) throw error;
      await wait(requestSignal);
      continue;
    }
    if (operation.status === 'complete') return operation;
    if (operation.status === 'unknown')
      throw new TerminalPreviewError('Исход запроса неизвестен. Автоматический повтор отключён.');
    if (operation.status === 'expired')
      throw new TerminalPreviewError('Срок хранения ответа истёк.');
    if (operation.status === 'unavailable')
      throw new TerminalPreviewError('Запрос остановлен до генерации. Проверьте настройки персонажа.');
    await wait(requestSignal);
  }
  throw requestSignal.reason;
}
