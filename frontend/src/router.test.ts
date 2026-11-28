import { describe, expect, it } from 'vitest';
import { parseChatsSearch, parseLeadsSearch, safeReturnUrl, searchText } from './router';

describe('search params', () => {
  it('chats keep the open chat, the search and the persona filter', () => {
    expect(parseChatsSearch({ id: 5, q: ' анна ', persona: 2 })).toEqual({ id: 5, q: 'анна', persona: 2 });
    expect(parseChatsSearch({ id: 'x', q: '', persona: 0 })).toEqual({});
  });

  it('chats keep the chosen messengers in one normalized form', () => {
    expect(parseChatsSearch({ source: 'instagram,icq,whatsapp' })).toEqual({ source: 'whatsapp,instagram' });
    expect(parseChatsSearch({ source: 'telegram_user,whatsapp,instagram' })).toEqual({});
    expect(parseChatsSearch({ source: 'icq' })).toEqual({});
  });

  it('leads keep the search, the persona and a known stage', () => {
    expect(parseLeadsSearch({ q: 'ann', persona: '3', stage: 'hot' })).toEqual({ q: 'ann', persona: 3, stage: 'hot' });
    expect(parseLeadsSearch({ stage: 'nope' })).toEqual({});
  });

  it('a numeric search typed by the operator stays text', () => {
    expect(searchText(380)).toBe('380');
    expect(searchText('   ')).toBeUndefined();
    expect(searchText({})).toBeUndefined();
  });

  it('post-login target stays inside the app', () => {
    expect(safeReturnUrl('/leads?q=1')).toBe('/leads?q=1');
    expect(safeReturnUrl('//evil.example')).toBeUndefined();
  });
});
