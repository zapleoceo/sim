import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Voice } from '@/lib/types';
import { fakeApi, reply } from '@/test/fake-api';
import { VoicePicker } from './voice-picker';

const VOICES: Voice[] = [
  { id: 'Kore', label: 'Kore', gender: 'female', tone: 'уверенный' },
  { id: 'Puck', label: 'Puck', gender: 'male', tone: 'бодрый' },
  { id: 'Aoede', label: 'Aoede', gender: 'female', tone: 'лёгкий' },
];

let played: HTMLMediaElement[] = [];

beforeEach(() => {
  played = [];
  Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:sample'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function play(this: HTMLMediaElement) {
    played.push(this);
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
});

afterEach(() => vi.restoreAllMocks());

function Picker({ initial = 'Kore', speed = 1 }: { initial?: string; speed?: number }) {
  const [value, setValue] = useState(initial);
  return <VoicePicker id="voice" value={value} onChange={setValue} voices={VOICES} personaId={4} speed={speed} />;
}

const radios = () => screen.getAllByRole('radio').map((r) => r.textContent);

describe('VoicePicker', () => {
  it('filters by gender and by search, and a row click chooses the voice', async () => {
    fakeApi({});
    render(<Picker />);
    expect(screen.getByText('Kore — уверенный, женский')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Kore/ })).toHaveAttribute('aria-checked', 'true');

    await userEvent.click(screen.getByRole('button', { name: 'Мужские' }));
    expect(radios()).toEqual(['Puck — бодрый, мужской']);
    // The chosen voice stays named above the list while the filter hides it.
    expect(screen.getByText('Kore — уверенный, женский')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Все' }));
    await userEvent.type(screen.getByRole('searchbox', { name: 'Поиск голоса' }), 'ЛЁГ');
    await waitFor(() => expect(radios()).toEqual(['Aoede — лёгкий, женский']));

    await userEvent.click(screen.getByRole('radio', { name: /Aoede/ }));
    expect(screen.getByRole('radio', { name: /Aoede/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('Aoede — лёгкий, женский', { selector: 'p > span' })).toBeInTheDocument();
  });

  it('plays a sample once fetched, at her speed with the pitch kept, and stops it', async () => {
    const log = fakeApi({ 'GET /api/voices/Puck/sample': 'RIFF' });
    render(<Picker speed={1.3} />);

    await userEvent.click(screen.getByRole('button', { name: 'Послушать Puck' }));
    const stop = await screen.findByRole('button', { name: 'Остановить Puck' });
    expect(log[0]).toMatchObject({ path: '/api/voices/Puck/sample', credentials: 'include' });
    expect(log[0]?.query.get('persona_id')).toBe('4');
    expect(played).toHaveLength(1);
    expect(played[0]?.playbackRate).toBe(1.3);
    expect(played[0]?.preservesPitch).toBe(true);

    await userEvent.click(stop);
    expect(screen.getByRole('button', { name: 'Послушать Puck' })).toBeInTheDocument();

    // A second play uses the fetched sample.
    await userEvent.click(screen.getByRole('button', { name: 'Послушать Puck' }));
    await screen.findByRole('button', { name: 'Остановить Puck' });
    expect(log).toHaveLength(1);
    expect(played).toHaveLength(2);
  });

  it('starting another sample stops the previous one', async () => {
    fakeApi({ 'GET /api/voices/Puck/sample': 'RIFF', 'GET /api/voices/Kore/sample': 'RIFF' });
    render(<Picker />);
    await userEvent.click(screen.getByRole('button', { name: 'Послушать Puck' }));
    await screen.findByRole('button', { name: 'Остановить Puck' });
    await userEvent.click(screen.getByRole('button', { name: 'Послушать Kore' }));
    await screen.findByRole('button', { name: 'Остановить Kore' });
    expect(screen.getByRole('button', { name: 'Послушать Puck' })).toBeInTheDocument();
  });

  it('shows why a sample could not be made, on its row', async () => {
    fakeApi({ 'GET /api/voices/Puck/sample': reply(409, { error: 'нет модели голоса' }) });
    render(<Picker />);
    await userEvent.click(screen.getByRole('button', { name: 'Послушать Puck' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось озвучить: нет модели голоса');
    expect(played).toHaveLength(0);
  });

  it('names a saved voice the list does not know', () => {
    fakeApi({});
    render(<Picker initial="Charon" />);
    expect(screen.getByText('Charon')).toBeInTheDocument();
    expect(screen.getAllByRole('radio').every((r) => r.getAttribute('aria-checked') === 'false')).toBe(true);
  });
});
