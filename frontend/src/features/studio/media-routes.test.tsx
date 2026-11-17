import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MediaRoutes } from './media-routes';

describe('dormant media routes', () => {
  it('shows blockers without a generate action', async () => {
    render(<MediaRoutes routes={[{ kind: 'photo', label: 'Фото', available: false,
      chain: [{ provider: 'Подписка Codex', model: '' },
        { provider: 'Google — резерв', model: 'gemini/synthetic-image' }],
      reason: 'Порт Google отсутствует.' }]} />);
    await userEvent.click(screen.getByText('Маршруты медиа — пока выключены'));
    expect(screen.getByText(/не проверка Broker/)).toBeVisible();
    expect(screen.getByText('Фото · Недоступно')).toBeVisible();
    expect(screen.getByText(/модель не выбрана/)).toBeVisible();
    expect(screen.getByText('Порт Google отсутствует.')).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('omits a plan withheld by the server', () => {
    const { container } = render(<MediaRoutes routes={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
