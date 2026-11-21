import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MutationSwitch, shownValue } from './mutation-switch';

describe('MutationSwitch', () => {
  it('shows the value being saved, otherwise the saved one', () => {
    expect(shownValue(true, false, false)).toBe(true);
    expect(shownValue(true, true, false)).toBe(false);
    expect(shownValue(true, true, undefined)).toBe(true);      // another field of the same mutation is saving
  });

  it('is a labelled switch, disabled while saving', async () => {
    const onChange = vi.fn();
    const { rerender } = render(<MutationSwitch checked={false} pending={false} onCheckedChange={onChange} label="ИИ в чате" />);
    await userEvent.click(screen.getByRole('switch', { name: 'ИИ в чате' }));
    expect(onChange).toHaveBeenCalledWith(true);

    rerender(<MutationSwitch checked={false} pending pendingValue onCheckedChange={onChange} label="ИИ в чате" />);
    expect(screen.getByRole('switch', { name: 'ИИ в чате' })).toBeChecked();
    expect(screen.getByRole('switch', { name: 'ИИ в чате' })).toBeDisabled();
  });

  it('without a label takes its name from aria-label', () => {
    render(<MutationSwitch checked pending={false} onCheckedChange={() => undefined} aria-label="ИИ отвечает клиенту Дима" />);
    expect(screen.getByRole('switch', { name: 'ИИ отвечает клиенту Дима' })).toBeChecked();
  });
});
