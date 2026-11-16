import { formatZonedDateTime } from './format';
import { voiceNotice } from './voice-notice';
import { eventValue } from '@/features/inbox/journal-logic';

it('summarizes technical voice reasons without inventing a handoff or live status', () => {
  expect(voiceNotice('Голос не воспроизведён: Контекст изменился во время проверки голоса'))
    .toBe('Разговор обновился. Старый голосовой ответ отменён.');
  expect(voiceNotice('Voice playback incomplete: 480/960 PCM bytes accepted'))
    .toContain('мог прозвучать не полностью');
  expect(voiceNotice('Оператор выключил ИИ')).toBeNull();
});

it('renders the same instant with an explicit user date and offset across day boundaries', () => {
  expect(formatZonedDateTime('2026-11-16T09:47:00Z', 'Asia/Ho_Chi_Minh')).toContain('16:47 GMT+7');
  expect(formatZonedDateTime('2026-11-16T09:47:00Z', 'Europe/Berlin')).toContain('10:47 GMT+1');
  expect(formatZonedDateTime('2026-12-31T18:47:00Z', 'Asia/Ho_Chi_Minh')).toContain('01.01.2027');
  expect(eventValue('reply_delay', 'reply_at:2026-11-16T09:47:00Z')).toContain('(ваше время)');
  expect(eventValue('reply_delay', 'ответ отложен до 10:47')).toContain('пояс в старой записи не сохранён');
});
