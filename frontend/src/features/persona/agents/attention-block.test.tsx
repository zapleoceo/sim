import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { AttentionSettings } from '@/lib/types';
import { AttentionBlock } from './attention-block';
import { DEFAULT_ATTENTION } from './attention-logic';

function setup(value: AttentionSettings = DEFAULT_ATTENTION) {
  const onChange = vi.fn<(next: AttentionSettings) => void>();
  render(<AttentionBlock value={value} onChange={onChange} />);
  return onChange;
}

describe('AttentionBlock', () => {
  it('shows the mode with its hint and switches the mode', async () => {
    const onChange = setup();
    const modes = screen.getByRole('group', { name: 'Внимание: режим' });
    expect(modes).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Как живой человек', pressed: true })).toBeInTheDocument();
    expect(screen.getByText(/Берёт телефон, когда позволяет её день/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Всегда сразу' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_ATTENTION, mode: 'instant' });
  });

  it('moves the pace slider within 0.5–2 and names the pace', () => {
    const onChange = setup({ ...DEFAULT_ATTENTION, pace: 1.6 });
    const slider = screen.getByLabelText(/Темп/);
    expect(slider).toHaveAttribute('min', '0.5');
    expect(slider).toHaveAttribute('max', '2');
    expect(screen.getByText('неторопливо ×1.6')).toBeInTheDocument();
    fireEvent.change(slider, { target: { value: '0.7' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_ATTENTION, pace: 0.7 });
  });

  it('turns a switch off and keeps the other settings', async () => {
    const onChange = setup();
    const dnd = screen.getByRole('switch', { name: 'Ночью «не беспокоить»' });
    expect(dnd).toBeChecked();
    await userEvent.click(dnd);
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_ATTENTION, night_dnd: false });
    expect(screen.getByRole('switch', { name: 'Может оставить прочитанным' })).toBeChecked();
    expect(screen.getByRole('switch', { name: 'Писать после пропущенного звонка' })).toBeChecked();
  });

  it('«Всегда сразу» has no pace and no switches to tune', () => {
    setup({ ...DEFAULT_ATTENTION, mode: 'instant' });
    expect(screen.getByLabelText(/Темп/)).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Ночью «не беспокоить»' })).toBeDisabled();
    expect(screen.getByText('В режиме «Всегда сразу» темп не действует.')).toBeInTheDocument();
  });
});
