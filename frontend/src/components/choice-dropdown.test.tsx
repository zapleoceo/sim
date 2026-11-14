import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import type { ClientStatus, Stage } from '@/lib/models';
import { StageDropdown, StatusDropdown } from './choice-dropdown';

describe('StageDropdown', () => {
  it('shows the current stage, lists every stage and applies the picked one with the reason', async () => {
    const applied: [Stage, string][] = [];
    function Probe() {
      const [stage, setStage] = useState<Stage>('engaged');
      return <StageDropdown stage={stage} onApply={(next, reason) => { applied.push([next, reason]); setStage(next); }} />;
    }
    const user = userEvent.setup();
    render(<Probe />);
    const trigger = screen.getByRole('combobox', { name: 'Этап' });
    expect(trigger).toHaveTextContent('Вовлечён');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    await user.click(trigger);
    const list = await screen.findByRole('listbox', { name: 'Этап' });
    expect(within(list).getAllByRole('option')).toHaveLength(7);
    expect(within(list).getByRole('option', { name: /Вовлечён/ })).toHaveAttribute('aria-selected', 'true');
    // Registration and payment come from partner data: shown, never picked by hand.
    expect(within(list).getByRole('option', { name: 'Регистрация' })).toBeDisabled();
    expect(within(list).getByRole('option', { name: 'Оплата' })).toBeDisabled();
    const apply = screen.getByRole('button', { name: 'Применить' });
    expect(apply).toBeDisabled();                 // the current stage is not a change

    await user.click(within(list).getByRole('option', { name: 'Интерес' }));
    expect(within(list).getByRole('option', { name: 'Интерес' })).toHaveAttribute('aria-selected', 'true');
    expect(applied).toEqual([]);                  // picking alone changes nothing
    await user.type(screen.getByLabelText('Причина (необязательно)'), '  созвон  ');
    await user.click(apply);

    expect(applied).toEqual([['interested', 'созвон']]);
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
    expect(screen.getByRole('combobox', { name: 'Этап' })).toHaveTextContent('Интерес');
  });

  it('sends an empty reason when none was typed and starts clean the next time', async () => {
    const applied: [Stage, string][] = [];
    const user = userEvent.setup();
    render(<StageDropdown stage="engaged" onApply={(next, reason) => { applied.push([next, reason]); }} />);
    await user.click(screen.getByRole('combobox', { name: 'Этап' }));
    await user.click(await screen.findByRole('option', { name: 'Потребность' }));
    await user.type(screen.getByLabelText('Причина (необязательно)'), 'черновик');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
    expect(applied).toEqual([]);

    await user.click(screen.getByRole('combobox', { name: 'Этап' }));
    expect(await screen.findByLabelText('Причина (необязательно)')).toHaveValue('');
    expect(screen.getByRole('option', { name: /Вовлечён/ })).toHaveAttribute('aria-selected', 'true');
    await user.click(screen.getByRole('option', { name: 'Интерес' }));
    await user.click(screen.getByRole('button', { name: 'Применить' }));
    expect(applied).toEqual([['interested', '']]);
  });

  it('keeps the list open when the change was not accepted', async () => {
    const user = userEvent.setup();
    render(<StageDropdown stage="engaged" onApply={() => Promise.resolve(false)} />);
    await user.click(screen.getByRole('combobox', { name: 'Этап' }));
    await user.click(await screen.findByRole('option', { name: 'Интерес' }));
    await user.click(screen.getByRole('button', { name: 'Применить' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Применить' })).toBeEnabled());
    expect(screen.getByRole('listbox', { name: 'Этап' })).toBeInTheDocument();
  });

  it('does not let a partner-confirmed client be moved by hand', () => {
    render(<StageDropdown stage="paid" onApply={() => undefined} />);
    expect(screen.getByRole('combobox', { name: 'Этап' })).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Этап' })).toHaveTextContent('Оплата');
  });
});

describe('StatusDropdown', () => {
  it('changes the status the same way: pick, optional reason, apply', async () => {
    const applied: [ClientStatus, string][] = [];
    const user = userEvent.setup();
    render(<StatusDropdown status="active" onApply={(next, reason) => { applied.push([next, reason]); }} />);
    expect(screen.getByRole('combobox', { name: 'Статус' })).toHaveTextContent('Активен');
    await user.click(screen.getByRole('combobox', { name: 'Статус' }));
    const list = await screen.findByRole('listbox', { name: 'Статус' });
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Активенсейчас', 'Пауза', 'Нет ответа', 'Отказ']);
    await user.click(within(list).getByRole('option', { name: 'Отказ' }));
    await user.type(screen.getByLabelText('Причина (необязательно)'), 'попросил не писать{Enter}');
    expect(applied).toEqual([['refused', 'попросил не писать']]);
  });
});
