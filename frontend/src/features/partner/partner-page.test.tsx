import { waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi } from '@/test/fake-api';
import { renderApp, screen } from '@/test/render-app';
import { MAX_CSV_BYTES, csvFileError } from './partner-logic';

describe('csvFileError', () => {
  it('accepts .csv up to 5 MB only', () => {
    expect(csvFileError({ name: 'export.CSV', size: 10 })).toBe('');
    expect(csvFileError({ name: 'export.xlsx', size: 10 })).toBe('Нужен файл .csv');
    expect(csvFileError({ name: 'big.csv', size: MAX_CSV_BYTES + 1 })).toBe('Файл больше 5 МБ');
  });
});

describe('PartnerPage', () => {
  it('rejects a non-CSV file', async () => {
    const user = userEvent.setup({ applyAccept: false });
    fakeApi({});
    await renderApp('/partner');
    await user.upload(await screen.findByLabelText('CSV-файл партнёра'), new File(['x'], 'data.txt', { type: 'text/plain' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Нужен файл .csv');
    expect(screen.getByRole('button', { name: 'Импортировать' })).toBeDisabled();
  });

  it('uploads the CSV and shows the result', async () => {
    const log = fakeApi({ 'POST /api/partner/import': { imported: 4, skipped: 1, errors: ['строка 3: нет client_ref'] } });
    const user = userEvent.setup();
    await renderApp('/partner');
    await user.upload(await screen.findByLabelText('CSV-файл партнёра'), new File(['a,b'], 'export.csv', { type: 'text/csv' }));
    await user.click(screen.getByRole('button', { name: 'Импортировать' }));

    expect(await screen.findByText('строка 3: нет client_ref')).toBeInTheDocument();
    expect(screen.getByText('импортировано').nextSibling).toHaveTextContent('4');
    const req = await waitFor(() => {
      const found = log.find((r) => r.method === 'POST' && r.path === '/api/partner/import');
      expect(found).toBeDefined();
      return found!;
    });
    expect(req.body).toBeInstanceOf(FormData);
    expect((req.body as FormData).get('file')).toBeInstanceOf(File);
  });
});
