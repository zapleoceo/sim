export const MAX_CSV_BYTES = 5 * 1024 * 1024;

export const CSV_HEADER = 'client_ref,event,amount,currency,payout,occurred_at,external_id';

/** Why a file cannot be imported ('' when it can). */
export function csvFileError(file: Pick<File, 'name' | 'size'>): string {
  if (!/\.csv$/i.test(file.name)) return 'Нужен файл .csv';
  if (file.size > MAX_CSV_BYTES) return 'Файл больше 5 МБ';
  return '';
}
