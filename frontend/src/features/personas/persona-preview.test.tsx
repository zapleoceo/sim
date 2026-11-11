import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi, reply } from '@/test/fake-api';
import { PersonaPreview } from './persona-preview';

const ok = { reply: 'Привет! Чем помочь?', model: 'fake-model', latency_ms: 42, decision: { stage: 'engaged' }, guard: null };

describe('PersonaPreview', () => {
  it('sends the message with history on submit without a native page submit', async () => {
    const log = fakeApi({ 'POST /api/personas/1/preview': ok });
    render(<PersonaPreview personaId={1} />);
    const input = screen.getByRole('textbox', { name: 'Сообщение' });

    fireEvent.change(input, { target: { value: 'привет' } });
    const notPrevented = fireEvent.submit(input.closest('form') as HTMLFormElement);
    expect(notPrevented).toBe(false);

    expect(await screen.findByText('Привет! Чем помочь?')).toBeInTheDocument();
    expect(screen.getByText('fake-model · 42 мс')).toBeInTheDocument();
    expect(log).toHaveLength(1);
    expect(log[0]?.body).toEqual({ message: 'привет', history: [] });

    await userEvent.type(input, 'а цена?{Enter}');
    await waitFor(() => expect(log).toHaveLength(2));
    expect(log[1]?.body).toEqual({
      message: 'а цена?',
      history: [
        { role: 'user', text: 'привет' },
        { role: 'assistant', text: 'Привет! Чем помочь?' },
      ],
    });
  });

  it('shows a preview failure visibly and «Повторить» resends the same message', async () => {
    let calls = 0;
    const log = fakeApi({
      'POST /api/personas/1/preview': () => (++calls === 1 ? reply(502, { detail: 'broker down' }) : ok),
    });
    render(<PersonaPreview personaId={1} />);
    await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'привет{Enter}');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Сервис временно недоступен');
    expect(alert).toHaveTextContent('broker down');
    expect(screen.getByText('привет')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByText('Привет! Чем помочь?')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(log).toHaveLength(2);
    expect(log[1]?.body).toEqual({ message: 'привет', history: [] });
  });

  it('marks a blocked reply and clears the chat', async () => {
    fakeApi({
      'POST /api/personas/1/preview': {
        ...ok,
        guard: { verdict: 'blocked', violations: ['обещание дохода'], model: 'guard', original_text: 'Гарантирую доход' },
      },
    });
    render(<PersonaPreview personaId={1} />);
    await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'сколько заработаю?{Enter}');
    expect(await screen.findByText('Ответ заблокирован контролером и не был бы отправлен.')).toBeInTheDocument();
    expect(screen.getByText('Заблокировано контролером')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Очистить' }));
    expect(screen.getByText('Напишите сообщение, как это сделал бы клиент.')).toBeInTheDocument();
  });
});
