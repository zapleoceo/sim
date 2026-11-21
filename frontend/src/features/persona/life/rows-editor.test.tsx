import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Place } from '@/lib/types';
import { newPlace } from './life-logic';
import { RowsEditor, gridMinRem, gridStep, type Column } from './rows-editor';

const ROUTINE_WIDTHS = ['15rem', '6.5rem', '6.5rem', 'minmax(10rem,2fr)', 'minmax(8rem,1fr)', 'minmax(7rem,1fr)', '8rem'];

describe('RowsEditor layout', () => {
  it('knows the width every column needs: tracks, the delete button, gaps and padding', () => {
    // 61 rem of columns + 2.25 delete + 7 gaps × 0.5 + 0.5 padding.
    expect(gridMinRem(ROUTINE_WIDTHS)).toBe(67.25);
    expect(gridMinRem(['minmax(8rem,1fr)', 'minmax(14rem,4fr)'])).toBe(25.75);
  });

  it('turns into a grid only from the container step that fits it', () => {
    expect(gridStep(67.25)[0]).toBe(68);
    expect(gridStep(25.75)[0]).toBe(32);
    expect(gridStep(74.75)[0]).toBe(76);                    // a day's events: wider than a 1080 px day card
    expect(gridStep(500)[0]).toBe(96);
    expect(gridStep(67.25)[1]).toContain('@min-[68rem]:[--rows-cols:var(--cols)]');
  });

  it('is a container: the grid depends on its own width, the rows are cards until then', () => {
    const columns: Column<Place>[] = [
      { key: 'name', label: 'Место', kind: 'text', width: 'minmax(8rem,1fr)' },
      { key: 'details', label: 'Подробности', kind: 'textarea', width: 'minmax(12rem,3fr)' },
    ];
    const { container } = render(
      <RowsEditor title="Места" rows={[{ ...newPlace(), name: 'кафе' }]} columns={columns} onChange={() => undefined}
        newRow={newPlace} addLabel="Добавить место" emptyText="Мест нет." />,
    );
    expect(container.firstElementChild).toHaveClass('@container');
    const table = screen.getByRole('group', { name: 'Места' });
    expect(table.className).toContain('@min-[32rem]:[--rows-head:grid]');
    expect(screen.getByLabelText('Место, строка 1')).toHaveValue('кафе');
  });
});
