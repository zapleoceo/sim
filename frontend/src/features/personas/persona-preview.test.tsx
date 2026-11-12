import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi, reply } from '@/test/fake-api';
import { PersonaPreview } from './persona-preview';

const initialContext = { stage: 'first_contact', status: 'active', needs: [], next_step: '' };

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
    expect(log[0]?.body).toEqual({ message: 'привет', history: [], voice_input: false, ...initialContext });

    await userEvent.type(input, 'а цена?{Enter}');
    await waitFor(() => expect(log).toHaveLength(2));
    expect(log[1]?.body).toEqual({
      message: 'а цена?',
      history: [
        { role: 'user', text: 'привет' },
        { role: 'assistant', text: 'Привет! Чем помочь?' },
      ],
      voice_input: false,
      ...initialContext,
    });
  });

  it('sends an attached photo as its Broker description and plays the voice reply', async () => {
    const log = fakeApi({
      'POST /api/personas/1/preview/attachment': {
        kind: 'image', name: 'check.jpg', text: '[изображение: чек на 500 грн]', media_url: '/api/media/abc.jpg',
      },
      'POST /api/personas/1/preview': {
        ...ok, reply: 'О, покупочка!', voice: { media_url: '/api/media/v.ogg', duration_s: 2, error: null },
      },
    });
    render(<PersonaPreview personaId={1} />);
    await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'смотри');
    await userEvent.upload(screen.getByTestId('preview-file'), new File(['jpeg'], 'check.jpg', { type: 'image/jpeg' }));

    expect(await screen.findByText('О, покупочка!')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'check.jpg' })).toHaveAttribute('src', '/api/media/abc.jpg');
    expect(screen.getByLabelText('Голосовой ответ')).toHaveAttribute('src', '/api/media/v.ogg');
    const sent = log.find((r) => r.path === '/api/personas/1/preview');
    expect(sent?.body).toEqual({ message: 'смотри\n[изображение: чек на 500 грн]', history: [], voice_input: false, ...initialContext });
    expect(log[0]?.body).toBeInstanceOf(FormData);
  });

  it('shows why a file could not be read and sends nothing', async () => {
    const log = fakeApi({
      'POST /api/personas/1/preview/attachment': reply(422, { detail: 'Поддерживаются голосовые, аудио, изображения и текстовые файлы' }),
    });
    render(<PersonaPreview personaId={1} />);
    await userEvent.upload(screen.getByTestId('preview-file'), new File(['x'], 'a.txt', { type: 'text/plain' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось распознать');
    expect(log.some((r) => r.path === '/api/personas/1/preview')).toBe(false);
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
    expect(log[1]?.body).toEqual({ message: 'привет', history: [], voice_input: false, ...initialContext });
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
