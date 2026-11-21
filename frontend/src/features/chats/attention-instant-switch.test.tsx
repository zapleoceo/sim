import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { AttentionInstantSwitch } from './attention-instant-switch';

describe('AttentionInstantSwitch', () => {
  it('turns «Отвечать сразу» on for this chat only', async () => {
    const log = fakeApi({ 'POST /api/chats/7/attention-instant': { id: 7, attention_instant: true } });
    await renderWithApp(<AttentionInstantSwitch chatId={7} enabled={false} />);
    const control = screen.getByRole('switch', { name: 'Отвечать сразу' });
    expect(control).not.toBeChecked();

    await userEvent.click(control);

    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(1));
    expect(log.find((r) => r.method === 'POST')).toMatchObject({
      path: '/api/chats/7/attention-instant', body: { enabled: true },
    });
  });

  it('shows the chat’s current state', async () => {
    fakeApi({});
    await renderWithApp(<AttentionInstantSwitch chatId={7} enabled />);
    expect(screen.getByRole('switch', { name: 'Отвечать сразу' })).toBeChecked();
  });
});
