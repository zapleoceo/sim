const KNOWN: [RegExp, string][] = [
  [/ChallengeRequired|challenge/i, 'Instagram просит подтвердить вход. Нажмите «Подключить» и введите код.'],
  [/LoginRequired|login_required|session.*(expired|invalid)/i, 'Сессия истекла. Подключите канал заново.'],
  [/BadPassword|password/i, 'Неверный логин или пароль.'],
  [/FloodWait|flood|rate.?limit|too many/i, 'Слишком много попыток. Подождите и повторите позже.'],
  [/Unauthorized|401|invalid.*token/i, 'Неверный токен или доступ отозван.'],
  [/timeout|timed out|connection/i, 'Нет связи с сервисом канала. Повторите позже.'],
];

/** Превращает сырую ошибку канала (`ChallengeRequired: …`) в понятный текст без имени класса исключения. */
export function humanizeChannelError(raw: string | null | undefined): string {
  if (!raw) return '';
  for (const [re, text] of KNOWN) if (re.test(raw)) return text;
  const stripped = raw.replace(/^\s*(?:[A-Za-z_][\w.]*(?:Error|Exception|Required|Failed|Flood\w*))\s*:\s*/, '').trim();
  return stripped.length > 160 ? `${stripped.slice(0, 157)}…` : stripped;
}
