import { QueryClient } from '@tanstack/react-query';
import { qk } from './query';

describe('query keys', () => {
  it('refreshes the inbox counter and test chats together with their families', async () => {
    const client = new QueryClient();
    client.setQueryData(qk.operatorQueue, 1);
    client.setQueryData(qk.inboxSimulations, []);
    await client.invalidateQueries({ queryKey: ['dialogs'] });
    await client.invalidateQueries({ queryKey: qk.simulations });
    expect(client.getQueryState(qk.operatorQueue)?.isInvalidated).toBe(true);
    expect(client.getQueryState(qk.inboxSimulations)?.isInvalidated).toBe(true);
  });

  it('shares one cache entry per channel list and test chat', () => {
    expect(qk.channels).toEqual(['channels']);
    expect(qk.simulation(7)).toEqual(['simulation', 7]);
  });
});
