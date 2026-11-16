import { promptDuplicates } from './prompt-duplicates';

describe('promptDuplicates', () => {
  it('finds nothing in a prompt that keeps to character, style and rules', () => {
    expect(promptDuplicates('')).toEqual([]);
    expect(promptDuplicates(
      'Ты Марина, психолог из Дрездена. Пишешь тепло и коротко, на «ты».\n'
      + 'Запреты: не обещай скидок, не называй цены до созвона.\n'
      + 'Работаешь с клиентами с 2015 года, у тебя двое детей.',
    )).toEqual([]);
  });

  it.each([
    ['an ISO date', 'Дата: 1976-12-09.'],
    ['a dotted date', 'Родилась 09.12.1976'],
    ['a date in words', 'Мой праздник 9 декабря 1976, Стрелец.'],
    ['«родилась»', 'Ты родилась в Харькове.'],
    ['«день рождения»', 'Твой день рождения зимой.'],
  ])('flags the birth date: %s', (_, text) => {
    expect(promptDuplicates(text)).toContain('дата рождения');
  });

  it.each([
    ['«NN лет»', 'Тебе 45 лет, ты спокойная.'],
    ['«NN года»', 'Ей 42 года.'],
    ['«мне NN»', 'Если спросят — мне 45, не скрываю.'],
  ])('flags the age: %s', (_, text) => {
    expect(promptDuplicates(text)).toEqual(['возраст']);
  });

  it('does not take years of a date, experience or longer words for an age', () => {
    expect(promptDuplicates('В 20 годах ты уехала.')).toEqual([]);
    expect(promptDuplicates('Мнение 45 человек')).toEqual([]);
    expect(promptDuplicates('Работаешь психологом 15 лет, замужем 20 лет.')).toEqual([]);
    expect(promptDuplicates('Возраст: 45')).toEqual(['возраст']);
  });

  it('flags the routine: its title or two schedule lines', () => {
    expect(promptDuplicates('РАСПОРЯДОК НЕДЕЛИ\nработа')).toEqual(['распорядок']);
    expect(promptDuplicates('пн-пт 09:00-13:00 консультации\nсб 10:00 – 12:00 бассейн')).toEqual(['распорядок']);
    // one line is a habit, not a copied schedule
    expect(promptDuplicates('пн-пт 09:00-13:00 консультации')).toEqual([]);
  });

  it('lists every duplicate once, in field order', () => {
    expect(promptDuplicates('Мне 45. Родилась 1980-01-02.\nпн 08:00-09:00 бег\nвт 08:00-09:00 бег\nТебе 45 лет'))
      .toEqual(['дата рождения', 'возраст', 'распорядок']);
  });
});
