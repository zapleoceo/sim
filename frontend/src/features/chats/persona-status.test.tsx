import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TooltipProvider } from '@/components/ui/tooltip';
import { qk } from '@/lib/query';
import type { PersonaStatus, PersonaStatusTone } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { PersonaStatusPill, PersonaStatusStrip } from './persona-status';
import { TEAM_ONLY, TONE_CLASS } from './persona-status-logic';

const status = (over: Partial<PersonaStatus> = {}): PersonaStatus => ({
  code: 'reply_later', tone: 'wait', title: 'Прочитала, ответит около 20:40', detail: 'после созвона',
  until: '2026-11-21T19:40:00Z', local_until: '20:40', ...over,
});

const TONES: PersonaStatusTone[] = ['ok', 'wait', 'busy', 'warn', 'off'];

afterEach(() => {
  vi.useRealTimers();
});

describe('PersonaStatusPill', () => {
  it('paints every tone in its own color and says it is for the team', () => {
    render(
      <TooltipProvider>
        {TONES.map((tone) => <PersonaStatusPill key={tone} status={status({ tone, title: `статус ${tone}` })} />)}
      </TooltipProvider>,
    );
    for (const tone of TONES) {
      const pill = screen.getByText(`статус ${tone}`).closest('[data-tone]');
      expect(pill).toHaveAttribute('data-tone', tone);
      for (const cls of TONE_CLASS[tone].split(' ')) expect(pill).toHaveClass(cls);
    }
    expect(screen.getAllByText('Статус для команды:', { exact: false })).toHaveLength(TONES.length);
  });
});

describe('PersonaStatusStrip', () => {
  it('shows the title, the reason and the end of the pause in her time, relative to now', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T19:28:00Z'));
    fakeApi({ 'GET /api/chats/7/status': status() });
    const free = status({ code: 'free', title: 'Свободна — заглянет в течение минуты', detail: 'без ответа: 1', until: null, local_until: null });
    await renderWithApp(<PersonaStatusStrip chatId={7} initial={free} />);

    const strip = await screen.findByRole('status');
    await within(strip).findByText('Прочитала, ответит около 20:40');
    expect(strip).toHaveAttribute('data-tone', 'wait');
    expect(within(strip).getByText('после созвона')).toBeInTheDocument();
    expect(within(strip).getByText('до 20:40 по её времени · через 12 мин')).toBeInTheDocument();
    expect(within(strip).getByLabelText(TEAM_ONLY)).toBeInTheDocument();
  });

  it('re-reads the status when the chat has a live event', async () => {
    let calls = 0;
    fakeApi({
      'GET /api/chats/7/status': () => {
        calls += 1;
        return calls === 1 ? status() : status({ code: 'thinking', tone: 'ok', title: 'Думает над ответом', detail: '', until: null, local_until: null });
      },
    });
    const { queryClient } = await renderWithApp(<PersonaStatusStrip chatId={7} initial={status()} />);
    const strip = await screen.findByRole('status');
    await within(strip).findByText('Прочитала, ответит около 20:40');

    await queryClient.invalidateQueries({ queryKey: qk.chatStatus(7) });

    await within(strip).findByText('Думает над ответом');
    expect(strip).toHaveAttribute('data-tone', 'ok');
    expect(within(strip).queryByText(/по её времени/)).not.toBeInTheDocument();
  });
});
