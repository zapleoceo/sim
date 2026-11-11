import { positiveInt } from './utils';

/** URL search params per screen; parsed defensively because users edit URLs. */

export interface LoginSearch { returnUrl?: string }
export interface InboxSearch { d?: number }
export interface ActivitySearch { tab?: 'llm'; channel_id?: number }

/** Only same-app paths are allowed as a post-login target (no open redirect). */
export function safeReturnUrl(value: unknown): string | undefined {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/login')
    ? value
    : undefined;
}

export function parseLoginSearch(s: Record<string, unknown>): LoginSearch {
  const returnUrl = safeReturnUrl(s.returnUrl);
  return returnUrl ? { returnUrl } : {};
}

export function parseInboxSearch(s: Record<string, unknown>): InboxSearch {
  const d = positiveInt(s.d);
  return d ? { d } : {};
}

export function parseActivitySearch(s: Record<string, unknown>): ActivitySearch {
  const out: ActivitySearch = {};
  if (s.tab === 'llm') out.tab = 'llm';
  const channel = positiveInt(s.channel_id);
  if (channel) out.channel_id = channel;
  return out;
}
