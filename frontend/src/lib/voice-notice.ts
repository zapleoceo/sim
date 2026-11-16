/** Plain-language summaries; raw diagnostics stay expandable, never change live status. */
export function voiceNotice(reason: string | null | undefined): string | null {
  if (!reason) return null;
  if (reason.includes('Контекст изменился во время проверки голоса') || reason.includes('Разговор обновился.')) {
    return 'Разговор обновился. Старый голосовой ответ отменён.';
  }
  if (reason.startsWith('Voice playback incomplete:')) return 'Голосовой ответ прерван. Он мог прозвучать не полностью.';
  if (reason.startsWith('Голос не воспроизведён:')) return 'Голосовой ответ не прошёл проверку и не прозвучал.';
  return null;
}
