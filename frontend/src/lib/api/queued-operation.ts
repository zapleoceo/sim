import { ApiError, http } from './http';

type OperationStatus<T> = { status: 'pending' | 'running' | 'unknown' | 'expired' | 'unavailable' } | T;

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

export async function pollQueued<T extends { status: 'complete' }>(
  path: string, signal: AbortSignal, input?: object, accepted?: (id: string) => string,
): Promise<T> {
  signal.throwIfAborted();
  const controller = new AbortController();
  const abort = () => controller.abort(signal.reason);
  signal.addEventListener('abort', abort, { once: true });
  const timeoutError = new PreviewOperationError('Ответ ещё не получен. Можно снова проверить тот же запрос.');
  const timer = window.setTimeout(() => controller.abort(timeoutError), 5 * 60_000);
  try {
    return await pollOperation<T>(path, controller.signal, input, accepted);
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}

async function pollOperation<T extends { status: 'complete' }>(
  path: string, requestSignal: AbortSignal, input?: object, accepted?: (id: string) => string,
): Promise<T> {
  const options = { silent: true, signal: requestSignal };
  let rejection: string | undefined;
  if (input) {
    try {
      const receipt = await http.post<{ id?: string }>(path, input, options);
      requestSignal.throwIfAborted();
      if (accepted && typeof receipt?.id === 'string') path = accepted(receipt.id);
    } catch (error) {
      requestSignal.throwIfAborted();
      if (!(error instanceof ApiError)) throw error;
      const detail = (error.body as { detail?: { status?: string } } | null)?.detail;
      if (detail?.status === 'queue_disabled') rejection = 'Очередь генерации временно недоступна.';
      else if (error.status === 429) rejection = 'Очередь заполнена. Дождитесь завершения активного запроса.';
      else if ([404, 409, 422].includes(error.status)) rejection = 'Сервер не принял новый запрос.';
      else if (error.status !== 0 && error.status < 500) throw error;
      // The origin may have committed despite a lost response. Consult the same ID.
    }
  }
  const acceptanceDeadline = Date.now() + 15_000;
  while (!requestSignal.aborted) {
    let operation: OperationStatus<T>;
    try {
      operation = await http.get<OperationStatus<T>>(path, options);
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
      throw new TerminalPreviewError('Исходные данные изменились или очередь недоступна.');
    await wait(requestSignal);
  }
  throw requestSignal.reason;
}
