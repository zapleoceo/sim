import { afterEach, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi, reply } from '@/test/fake-api';
import { PersonaPreview } from './persona-preview';
import { rememberPreview } from './preview-operation';

const defaultStages = { schedule: false, debounce: false, decision_context: true };
const initialContext = { stage: 'first_contact', status: 'active', needs: [], next_step: '' };

const ok = { reply: 'Привет! Чем помочь?', model: 'fake-model', latency_ms: 42, decision: { stage: 'engaged' }, guard: null };

afterEach(() => sessionStorage.clear());

describe('PersonaPreview', () => {
  it('shows persona-scoped gallery photos and a bundled video circle without sending a reply', async () => {
    const log = fakeApi({
      'GET /api/personas/1/media': {
        items: [
          { id: 3, kind: 'video_note' },
          { id: 7, kind: 'photo', file_url: 'https://example.invalid/untrusted.jpg' },
        ],
      },
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PersonaPreview personaId={1} />
      </QueryClientProvider>,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Показать фото из галереи' }));
    expect(await screen.findByRole('img', { name: 'Фото из галереи персонажа' }))
      .toHaveAttribute('src', '/api/personas/1/media/7/file');

    await userEvent.click(screen.getByRole('button', { name: 'Показать тестовый кружок' }));
    const video = screen.getByLabelText('Тестовый видеокружок');
    expect(video.tagName).toBe('VIDEO');
    expect(video).toHaveAttribute('src', '/preview-circle.mp4');
    expect(video).toHaveAttribute('controls');
    expect(video.parentElement).toHaveClass('rounded-full');
    expect(screen.queryByRole('img', { name: 'Фото из галереи персонажа' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Очистить' }));
    expect(screen.queryByLabelText('Тестовый видеокружок')).not.toBeInTheDocument();
    expect(log.map((entry) => entry.path)).toEqual(['/api/personas/1/media']);
  });

  it('restores an accepted operation after mount without submitting another request', async () => {
    const key = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
    const path = `/api/personas/1/preview-operations/${key}`;
    rememberPreview(1, key);
    const log = fakeApi({
      [`GET ${path}`]: { status: 'complete', history: [], message: 'Привет', result: ok },
    });
    render(<PersonaPreview personaId={1} />);
    expect(await screen.findByText(ok.reply)).toBeInTheDocument();
    expect(screen.getByText('Привет')).toBeInTheDocument();
    expect(log.map(({ method, path }) => [method, path])).toEqual([['GET', path]]);
  });

  it('keeps the existing voiced route by default when the queue is available', async () => {
    const log = fakeApi({ 'POST /api/personas/1/preview': ok });
    render(<PersonaPreview personaId={1} asyncEnabled />);
    expect(screen.getByRole('combobox', { name: 'Режим предпросмотра' })).toHaveValue('voice');
    await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Привет{enter}');
    expect(await screen.findByText(ok.reply)).toBeInTheDocument();
    expect(log.map(({ method, path }) => [method, path])).toEqual([['POST', '/api/personas/1/preview']]);
  });

  it('asks for an explicit mode change if the queue is switched off', async () => {
    const log = fakeApi({});
    const view = render(<PersonaPreview personaId={1} asyncEnabled />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Режим предпросмотра' }), 'queued');
    view.rerender(<PersonaPreview personaId={1} />);
    expect(screen.getByRole('status')).toHaveTextContent('Очередь отключена');
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Режим предпросмотра' }), 'voice');
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeEnabled();
    expect(log).toHaveLength(0);
  });

  it('shows restored text before the separately queued audio is ready', async () => {
    const key = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
    const path = `/api/personas/1/preview-operations/${key}`;
    const audio = `${path}/audio`;
    rememberPreview(1, key);
    let reads = 0;
    const log = fakeApi({
      [`GET ${path}`]: () => ({
        status: 'complete', history: [], message: 'Привет',
        result: { ...ok, voice: reads++ === 0
          ? { status: 'pending', media_url: null, duration_s: null, error: null }
          : { status: 'done', media_url: audio, duration_s: null, error: null } },
      }),
    });
    render(<PersonaPreview personaId={1} asyncEnabled speechJobsReady />);
    expect(await screen.findByText(ok.reply)).toBeVisible();
    expect(screen.getByText('Готовим озвучку…')).toBeVisible();
    expect(screen.queryByRole('combobox', { name: 'Режим предпросмотра' })).not.toBeInTheDocument();
    expect(await screen.findByLabelText('Голосовой ответ', {}, { timeout: 4000 })).toHaveAttribute('src', audio);
    expect(screen.getAllByText(ok.reply)).toHaveLength(1);
    expect(log.map(({ method }) => method)).toEqual(['GET', 'GET']);
  });

  it('allows the next message and attaches late audio to the original reply', async () => {
    const firstKey = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
    const nextKey = '1795171200000-d7dd86f1-1943-4fcb-bc89-8c839db95995';
    const firstPath = `/api/personas/1/preview-operations/${firstKey}`;
    const nextPath = `/api/personas/1/preview-operations/${nextKey}`;
    const audio = `${firstPath}/audio`;
    rememberPreview(1, firstKey);
    let finish: (value: object) => void = () => undefined;
    const ready = new Promise<object>((resolve) => { finish = resolve; });
    let reads = 0;
    fakeApi({
      [`GET ${firstPath}`]: () => reads++ === 0
        ? { status: 'complete', history: [], message: 'Первый вопрос',
          result: { ...ok, reply: 'Первый ответ',
            voice: { status: 'pending', media_url: null, duration_s: null, error: null } } }
        : ready,
      [`POST ${nextPath}`]: reply(202, { id: nextKey }),
      [`GET ${nextPath}`]: { status: 'complete', history: [], message: 'Второй вопрос',
        result: { ...ok, reply: 'Второй ответ' } },
    });
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1795171200000);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue('d7dd86f1-1943-4fcb-bc89-8c839db95995');
    try {
      render(<PersonaPreview personaId={1} asyncEnabled speechJobsReady />);
      const first = await screen.findByText('Первый ответ');
      await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Второй вопрос{enter}');
      const next = await screen.findByText('Второй ответ');
      finish({ status: 'complete', history: [], message: 'Первый вопрос',
        result: { ...ok, voice: { status: 'done', media_url: audio, duration_s: null, error: null } } });
      expect(await within(first.parentElement!).findByLabelText('Голосовой ответ', {}, { timeout: 4000 }))
        .toHaveAttribute('src', audio);
      expect(within(next.parentElement!).queryByLabelText('Голосовой ответ')).not.toBeInTheDocument();
    } finally {
      clock.mockRestore();
      uuid.mockRestore();
    }
  });

  it('restores a private attachment and sends it only through private preview', async () => {
    const key = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
    const path = `/api/personas/1/preview/attachment-operations/${key}`;
    const previewPath = `/api/personas/1/preview-operations/${key}`;
    sessionStorage.setItem('sim.preview.attachment.0.1', key);
    const log = fakeApi({
      [`GET ${path}`]: { status: 'complete', result: {
        kind: 'file', name: 'sample.txt', text: '[файл sample.txt] Пример', media_url: `${path}/file`,
      } },
      [`POST ${previewPath}`]: reply(202, { id: key }),
      [`GET ${previewPath}`]: { status: 'complete', result: ok, history: [], message: 'Уточнение' },
    });
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1795171200000);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue('9d80c89d-438e-4eef-8848-7bfb39a89af3');
    try {
      render(<PersonaPreview personaId={1} attachmentQueueEnabled />);
      expect(await screen.findByText('[файл sample.txt] Пример')).toBeVisible();
      expect(log.map(({ method }) => method)).toEqual(['GET']);
      await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Уточнение');
      await userEvent.click(screen.getByRole('button', { name: 'Отправить' }));
      await screen.findByText(ok.reply);
      const outbound = log.find(({ method }) => method === 'POST');
      expect(outbound?.path).toBe(previewPath);
      expect(outbound?.body).toMatchObject({ message: 'Уточнение\n[файл sample.txt] Пример' });
    } finally {
      clock.mockRestore();
      uuid.mockRestore();
      sessionStorage.removeItem('sim.preview.attachment.0.1');
      sessionStorage.removeItem('sim.preview.pending.0.1');
      sessionStorage.removeItem('sim.preview.private-history.0.1');
    }
  });

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
    expect(log[0]?.body).toEqual({ message: 'привет', history: [], voice_input: false, ...initialContext, stages: defaultStages });

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
      stages: defaultStages,
    });
  });

  it('sends only this test chat’s stage choices and shows the returned estimate without waiting', async () => {
    let calls = 0;
    const log = fakeApi({
      'POST /api/personas/1/preview': () => (++calls === 1 ? {
        ...ok,
        stages: {
          schedule: { enabled: true, wait_s: 3600, reason: 'на работе' },
          debounce: { enabled: true, wait_s: 8 },
          estimated_wait_s: 3600,
          decision_context: false,
        },
      } : { ...ok, reply: 'Второй ответ' }),
    });
    render(<PersonaPreview personaId={1} />);
    const context = screen.getByRole('checkbox', { name: /решения прошлых реплик/ });
    expect(context).toBeChecked();
    expect(screen.getByText(/отдельные агенты планирования здесь не запускаются/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: /по расписанию/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: /паузу перед ответом/ }));
    await userEvent.click(context);
    const input = screen.getByRole('textbox', { name: 'Сообщение' });
    await userEvent.type(input, 'проверка{Enter}');

    expect(await screen.findByText('Привет! Чем помочь?')).toBeInTheDocument();
    expect(log[0]?.body).toMatchObject({
      stages: { schedule: true, debounce: true, decision_context: false },
    });
    await userEvent.click(screen.getByText('Этапы ответа'));
    expect(screen.getByText(/3600 с.*в тесте не ждали/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Основной промпт без контекста решений' }));
    await userEvent.type(input, 'ещё раз{Enter}');
    expect(await screen.findByText('Второй ответ')).toBeInTheDocument();
    expect(log[1]?.body).toMatchObject({
      stages: { schedule: false, debounce: false, decision_context: false },
      history: [{ role: 'user', text: 'проверка' }, { role: 'assistant', text: ok.reply }],
    });
    await userEvent.click(screen.getByRole('button', { name: 'Очистить' }));
    expect(context).toBeChecked();
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
    expect(sent?.body).toEqual({ message: 'смотри\n[изображение: чек на 500 грн]', history: [], voice_input: false, ...initialContext, stages: defaultStages });
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
    expect(log[1]?.body).toEqual({ message: 'привет', history: [], voice_input: false, ...initialContext, stages: defaultStages });
  });

  it('keeps private history off the legacy route even when retry prefers it', async () => {
    const key = '1795171200000-d7dd86f1-1943-4fcb-bc89-8c839db95995';
    const path = `/api/personas/1/preview-operations/${key}`;
    const log = fakeApi({
      'POST /api/personas/1/preview': reply(502),
      [`POST ${path}`]: reply(202, { id: key }),
      [`GET ${path}`]: { status: 'complete', history: [], message: 'Вопрос', result: ok },
    });
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1795171200000);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue('d7dd86f1-1943-4fcb-bc89-8c839db95995');
    try {
      render(<PersonaPreview personaId={1} />);
      await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Вопрос{Enter}');
      const retry = await screen.findByRole('button', { name: 'Повторить' });
      sessionStorage.setItem('sim.preview.private-history.0.1', '1');
      await userEvent.click(retry);
      expect(await screen.findByText(ok.reply)).toBeVisible();
      expect(log.filter(({ method }) => method === 'POST').map(({ path: url }) => url))
        .toEqual(['/api/personas/1/preview', path]);
    } finally {
      clock.mockRestore();
      uuid.mockRestore();
      sessionStorage.removeItem('sim.preview.private-history.0.1');
    }
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
