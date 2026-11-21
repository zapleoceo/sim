import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Media, TimelineItem, TimelineMessage } from '@/lib/types';
import { TimelineEntry } from './timeline-entry';
import { lastMessagePreview, systemLine } from './timeline-logic';

const AT = '2026-11-21T10:05:00Z';

function item(patch: Partial<TimelineItem>): TimelineItem {
  return { id: 1, kind: 'message_in', actor: 'client', created_at: AT, in_context: true, reply_run_id: null, data: {}, ...patch };
}

function message(patch: Partial<TimelineMessage>): TimelineMessage {
  return { id: 10, direction: 'in', author: 'client', kind: 'text', text: 'привет', status: 'received', error: null, sent_at: AT, ...patch };
}

function media(patch: Partial<Media>): Media {
  return {
    id: 5, kind: 'photo', mime: 'image/jpeg', url: '/api/media/5/file', caption: '', tags: [], description: '',
    source: 'incoming', is_reference: false, width: null, height: null, duration_s: null, file_name: '', ...patch,
  };
}

const show = (entry: TimelineItem, onExplain = vi.fn()) => render(<TimelineEntry item={entry} onExplain={onExplain} />);

describe('TimelineEntry', () => {
  it('client text on the left, no «Почему так»', () => {
    const { container } = show(item({ message: message({}) }));
    expect(screen.getByText('привет')).toBeInTheDocument();
    expect(container.querySelector('[data-author="client"]')).toHaveClass('self-start');
    expect(screen.queryByRole('button', { name: 'Почему так' })).not.toBeInTheDocument();
  });

  it('persona reply on the right with «Почему так» opening its run', async () => {
    const onExplain = vi.fn();
    const { container } = show(
      item({ kind: 'message_out', reply_run_id: 77, message: message({ direction: 'out', author: 'persona', text: 'ну привет', status: 'sent' }) }),
      onExplain,
    );
    expect(container.querySelector('[data-author="persona"]')).toHaveClass('self-end');
    expect(screen.queryByText('оператор')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Почему так' }));
    expect(onExplain).toHaveBeenCalledWith(77);
  });

  it('operator message on the right with the «оператор» label', () => {
    show(item({ kind: 'message_out', message: message({ direction: 'out', author: 'operator', text: 'это я', status: 'sent' }) }));
    expect(screen.getByText('оператор')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Почему так' })).not.toBeInTheDocument();
  });

  it('her phone check is a small gray line with the reason', () => {
    show(item({
      kind: 'attention', actor: 'persona', in_context: false,
      data: { action: 'reply_later', who: 'Марина', later: '18:40', reason: 'после рынка', until_event_id: 3 },
    }));
    const line = screen.getByText(/Марина: прочитала, ответит позже в 18:40 \(после рынка\)/);
    expect(line).toHaveClass('text-muted-foreground');
    expect(systemLine(item({ kind: 'attention', data: { action: 'leave_unread', who: 'Марина' } }))?.text)
      .toBe('Марина: не открыла');
    expect(systemLine(item({ kind: 'attention', data: { action: 'call_back', reason: 'проснулась' } }))?.text)
      .toBe('Она: напишет после пропущенного звонка (проснулась)');
  });

  it('a failed send is red with the error', () => {
    show(item({
      kind: 'send_failed',
      message: message({ direction: 'out', author: 'persona', status: 'failed', error: 'PEER_FLOOD' }),
    }));
    expect(screen.getByRole('alert')).toHaveTextContent('Не доставлено: PEER_FLOOD');
  });

  it('photo with its description', () => {
    show(item({ message: message({ kind: 'photo', text: null, media: media({ description: 'кот на окне' }) }) }));
    expect(screen.getByRole('img', { name: 'кот на окне' })).toHaveAttribute('src', '/api/media/5/file');
    expect(screen.getByText('кот на окне', { selector: 'span' })).toBeInTheDocument();
  });

  it('video as a <video controls>', () => {
    const { container } = show(item({ message: message({ kind: 'video', text: null, media: media({ kind: 'video', mime: 'video/mp4' }) }) }));
    const video = container.querySelector('video');
    expect(video).toHaveAttribute('controls');
    expect(video).toHaveAttribute('src', '/api/media/5/file');
  });

  it('voice as <audio controls> with the transcript below', () => {
    const { container } = show(item({
      message: message({ kind: 'voice', text: null, media: media({ kind: 'audio', mime: 'audio/ogg', duration_s: 14, description: 'перезвони мне' }) }),
    }));
    expect(container.querySelector('audio')).toHaveAttribute('controls');
    expect(screen.getByText('Расшифровка:')).toBeInTheDocument();
    expect(screen.getByText('перезвони мне')).toBeInTheDocument();
    expect(screen.getByText('0:14')).toBeInTheDocument();
  });

  it('file chip with its name and summary', () => {
    show(item({ message: message({ kind: 'file', text: null, media: media({ kind: 'file', mime: 'application/pdf', file_name: 'договор.pdf', description: 'аренда на год' }) }) }));
    expect(screen.getByRole('link', { name: 'договор.pdf' })).toHaveAttribute('href', '/api/media/5/file');
    expect(screen.getByText('аренда на год')).toBeInTheDocument();
  });

  it('call card with direction, duration, outcome and transcript', () => {
    show(item({
      kind: 'call',
      call: { id: 3, direction: 'in', status: 'ended', duration_s: 192, transcript: [{ who: 'client', text: 'алло', t: 1 }] },
    }));
    expect(screen.getByText('Входящий звонок')).toBeInTheDocument();
    expect(screen.getByText(/3:12 · завершён/)).toBeInTheDocument();
    expect(screen.getByText('Расшифровка (1)')).toBeInTheDocument();
    expect(screen.getByText('алло')).toBeInTheDocument();
  });

  it('generation chip: running and failed', () => {
    const { rerender } = show(item({
      kind: 'generation_started',
      generation: { id: 1, kind: 'video', status: 'running', caption: 'у окна', error: null },
    }));
    expect(screen.getByText(/Генерация видео «у окна» — готовится/)).toBeInTheDocument();
    rerender(
      <TimelineEntry
        item={item({ kind: 'generation_failed', generation: { id: 1, kind: 'video', status: 'failed', caption: 'у окна', error: 'таймаут' } })}
        onExplain={vi.fn()}
      />,
    );
    expect(screen.getByText(/не получилось/)).toBeInTheDocument();
    expect(screen.getByText('таймаут')).toHaveClass('text-destructive');
    rerender(
      <TimelineEntry
        item={item({ kind: 'generation_started', generation: { id: 2, kind: 'video_note', status: 'running', caption: 'привет', error: null } })}
        onExplain={vi.fn()}
      />,
    );
    expect(screen.getByText(/Генерация кружка «привет»/)).toBeInTheDocument();
  });

  it('journal events as small centred lines', () => {
    show(item({ kind: 'guard_blocked', actor: 'system', data: { reason: 'призналась, что бот' } }));
    expect(screen.getByText(/Проверка заблокировала ответ: призналась, что бот/)).toHaveClass('self-center');
  });

  it('media_understood renders nothing (it fills its message)', () => {
    const { container } = show(item({ kind: 'media_understood' }));
    expect(container).toBeEmptyDOMElement();
  });
});

describe('systemLine', () => {
  it('describes each journal kind', () => {
    expect(systemLine(item({ kind: 'operator_needed', data: { reason: 'две блокировки' } }))).toEqual({ text: 'Нужен оператор: две блокировки', tone: 'warning' });
    expect(systemLine(item({ kind: 'ai_toggled', data: { enabled: false } }))?.text).toBe('ИИ выключен в этом чате');
    expect(systemLine(item({ kind: 'ai_toggled', data: { enabled: true } }))?.text).toBe('ИИ включён в этом чате');
    expect(systemLine(item({ kind: 'operator_note', data: { text: 'не звать на встречу' } }))?.text).toBe('Заметка оператора: не звать на встречу');
    expect(systemLine(item({ kind: 'memory_updated' }))?.text).toBe('Сводка переписки обновлена');
    expect(systemLine(item({ kind: 'message_out' }))).toBeNull();
  });
});

describe('lastMessagePreview', () => {
  it('prefixes the author and names media', () => {
    expect(lastMessagePreview(null)).toBe('Нет сообщений');
    expect(lastMessagePreview({ kind: 'text', direction: 'in', author: 'client', text: 'ау' })).toBe('ау');
    expect(lastMessagePreview({ kind: 'photo', direction: 'out', author: 'persona', text: null })).toBe('Она: Фото');
    expect(lastMessagePreview({ kind: 'voice', direction: 'out', author: 'operator', text: 'да' })).toBe('Оператор: Голосовое · да');
  });
});
