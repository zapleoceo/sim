import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { ChatFlagSwitch } from './chat-flag-switch';

describe('ChatFlagSwitch', () => {
  it('turns «Отвечать сразу» on for this chat only', async () => {
    const log = fakeApi({ 'POST /api/chats/7/attention-instant': { id: 7, attention_instant: true } });
    await renderWithApp(<ChatFlagSwitch chatId={7} flag="attention_instant" enabled={false} />);
    const control = screen.getByRole('switch', { name: 'Отвечать сразу' });
    expect(control).not.toBeChecked();

    await userEvent.click(control);

    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(1));
    expect(log.find((r) => r.method === 'POST')).toMatchObject({
      path: '/api/chats/7/attention-instant', body: { enabled: true },
    });
  });

  it('turns «Без отказов» on for this chat', async () => {
    const log = fakeApi({ 'POST /api/chats/7/no-refusals': { id: 7, no_refusals: true } });
    await renderWithApp(<ChatFlagSwitch chatId={7} flag="no_refusals" enabled={false} />);

    await userEvent.click(screen.getByRole('switch', { name: 'Без отказов' }));

    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(1));
    expect(log.find((r) => r.method === 'POST')).toMatchObject({
      path: '/api/chats/7/no-refusals', body: { enabled: true },
    });
  });

  it('turns «Звонит сама» on for this chat', async () => {
    const log = fakeApi({ 'POST /api/chats/7/calls-out': { id: 7, calls_out: true } });
    await renderWithApp(<ChatFlagSwitch chatId={7} flag="calls_out" enabled={false} />);

    await userEvent.click(screen.getByRole('switch', { name: 'Звонит сама' }));

    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(1));
    expect(log.find((r) => r.method === 'POST')).toMatchObject({
      path: '/api/chats/7/calls-out', body: { enabled: true },
    });
  });

  it('shows the chat’s current state', async () => {
    fakeApi({});
    await renderWithApp(<ChatFlagSwitch chatId={7} flag="attention_instant" enabled />);
    expect(screen.getByRole('switch', { name: 'Отвечать сразу' })).toBeChecked();
  });
});
