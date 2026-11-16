/**
 * What of the persona's own fields the prompt repeats. The birth date, the time zone and the routine live in
 * their fields: code counts her age, local time and where she is now from them and adds that to the model.
 * The same facts written into the prompt go stale and contradict the fields, so the page warns about them.
 */

const MONTHS = 'января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря';
/** `\b` is ASCII-only in JS: Cyrillic words need their own boundaries. */
const START = '(?<![\\p{L}\\d])';
const END = '(?![\\p{L}\\d])';

const BIRTH_DATE: readonly RegExp[] = [
  /(?<!\d)(?:19|20)\d{2}-\d{2}-\d{2}(?!\d)/u,
  /(?<!\d)\d{1,2}\.\d{1,2}\.(?:19|20)\d{2}(?!\d)/u,
  new RegExp(`${START}\\d{1,2}\\s+(?:${MONTHS})\\s+(?:19|20)\\d{2}${END}`, 'iu'),
  new RegExp(`${START}(?:родилась|родился|день рождения|дата рождения)${END}`, 'iu'),
];

/** Her own age only: «мне/тебе/ей (уже) 45 (лет)», «возраст 45»; «работаю 15 лет» is not an age. */
const AGE: readonly RegExp[] = [
  new RegExp(`${START}(?:мне|тебе|ей|возраст)(?:\\s+уже)?[\\s:—-]+\\d{2}${END}`, 'iu'),
];

const ROUTINE_TITLE = /распорядок недели/iu;
/** A routine line: days, then a time range, e.g. «пн-пт 09:00-13:00 консультации». */
const ROUTINE_LINE = new RegExp(
  `^\\s*[-•*]?\\s*(?:пн|вт|ср|чт|пт|сб|вс|ежедневно)${END}[^\\n]*?\\d{1,2}:\\d{2}\\s*[-–—]\\s*\\d{1,2}:\\d{2}`,
  'gimu',
);

const matchesAny = (text: string, patterns: readonly RegExp[]): boolean => patterns.some((p) => p.test(text));

/** Russian labels of the fields the prompt duplicates, in field order; empty when the prompt is clean. */
export function promptDuplicates(text: string): string[] {
  const found: string[] = [];
  if (matchesAny(text, BIRTH_DATE)) found.push('дата рождения');
  if (matchesAny(text, AGE)) found.push('возраст');
  if (ROUTINE_TITLE.test(text) || (text.match(ROUTINE_LINE)?.length ?? 0) >= 2) found.push('распорядок');
  return found;
}
