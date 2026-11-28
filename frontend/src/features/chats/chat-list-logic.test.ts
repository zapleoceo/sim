import { describe, expect, it } from 'vitest';
import { emptyChatsText } from './chat-list-logic';

describe('the empty chat list', () => {
  it('names the search and the messengers', () => {
    expect(emptyChatsText({ q: 'анна', sources: ['whatsapp'], persona: 0 })).toMatchObject({
      title: 'Ничего не найдено по «анна» в WhatsApp', filtered: true,
    });
    expect(emptyChatsText({ q: '', sources: ['telegram_user', 'whatsapp', 'instagram'], persona: 0 }).title)
      .toBe('Чатов в Telegram, WhatsApp и Instagram пока нет');
    expect(emptyChatsText({ q: '', sources: [], persona: 2 })).toMatchObject({ title: 'Чатов пока нет', filtered: true });
  });

  it('without filters it is the very first state', () => {
    expect(emptyChatsText({ q: '', sources: [], persona: 0 })).toMatchObject({ title: 'Чатов пока нет', filtered: false });
  });
});
