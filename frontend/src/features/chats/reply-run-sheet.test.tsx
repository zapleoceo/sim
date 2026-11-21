import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ReplyRun } from '@/lib/types';
import { renderWithApp } from '@/test/render';
import { ReplyRunView, contextBlocks, contextDays } from './reply-run-sheet';

const CONTEXT = {
  facts: ['Сейчас у тебя: суббота'],
  now: '# ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС',
  routine: '# ТВОЙ РАСПОРЯДОК',
  client: 'Имя: Дима',
  days: [
    { day: '2026-11-19', summary: 'Познакомились.', source: 'model' },
    { day: '2026-11-20', summary: 'Дима рассказал про работу.', source: 'edited' },
  ],
  timeline: ['[21.11 20:27] Дима: привет'],
  notes: ['Есть дочь Аня'],
  catalog: 'Можно отправить: ничего',
};

function run(context: Record<string, unknown> | null): ReplyRun {
  return { id: 5, status: 'sent', context, plan: {}, guard_verdicts: [], error: null, llm_calls: [] };
}

describe('«Почему так»', () => {
  it('orders the context blocks from stable to volatile', () => {
    expect(contextBlocks(CONTEXT).map((b) => b.key)).toEqual(
      ['routine', 'client', 'days', 'timeline', 'notes', 'facts', 'now', 'catalog']);
  });

  it('reads the day summaries that were in the context', () => {
    expect(contextDays(CONTEXT)).toEqual([
      { day: '2026-11-19', summary: 'Познакомились.' },
      { day: '2026-11-20', summary: 'Дима рассказал про работу.' }]);
    expect(contextDays({ days: [{ day: 1 }, null, 'x'] })).toEqual([]);
    expect(contextDays(null)).toEqual([]);
  });

  it('shows which days were in the context', async () => {
    await renderWithApp(<ReplyRunView run={run(CONTEXT)} />);
    const section = (await screen.findByRole('heading', { name: 'Дни в контексте' })).closest('section');
    expect(section).not.toBeNull();
    const items = within(section as HTMLElement).getAllByRole('listitem');
    expect(items.map((li) => li.textContent)).toEqual([
      'четверг, 19 ноября: Познакомились.', 'пятница, 20 ноября: Дима рассказал про работу.']);
  });

  it('says so when no past day was in the context', async () => {
    await renderWithApp(<ReplyRunView run={run({ facts: [] })} />);
    expect(await screen.findByText('Итогов прошлых дней в контексте не было.')).toBeInTheDocument();
  });
});
