import { describe, expect, it } from 'vitest';
import { parseSources, sourcesParam, toggleSource } from './sources-logic';

describe('source filter logic', () => {
  it('reads known messengers once, in the chip order', () => {
    expect(parseSources('instagram, whatsapp,icq,whatsapp')).toEqual(['whatsapp', 'instagram']);
    expect(parseSources(['telegram_user', 'nope'])).toEqual(['telegram_user']);
    expect(parseSources(undefined)).toEqual([]);
    expect(parseSources(5)).toEqual([]);
  });

  it('writes nothing for no choice or for every messenger', () => {
    expect(sourcesParam([])).toBeUndefined();
    expect(sourcesParam(['instagram', 'telegram_user', 'whatsapp'])).toBeUndefined();
    expect(sourcesParam(['instagram', 'whatsapp'])).toBe('whatsapp,instagram');
  });

  it('a chip adds or removes its messenger', () => {
    expect(toggleSource([], 'instagram')).toEqual(['instagram']);
    expect(toggleSource(['instagram'], 'telegram_user')).toEqual(['telegram_user', 'instagram']);
    expect(toggleSource(['telegram_user', 'instagram'], 'telegram_user')).toEqual(['instagram']);
  });
});
