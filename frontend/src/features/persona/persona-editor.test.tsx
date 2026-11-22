import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { setCsrfToken } from '@/lib/api';
import type { Persona, Voice } from '@/lib/types';
import { fakeApi, type FakeRequest } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { PERSONA_FORM_ID, PersonaEditor } from './persona-editor';
import { olderThanDays, personaFormErrors, toFormValues, toPatch } from './persona-form-logic';

const PERSONA: Persona = {
  id: 1, name: 'Марина', gender: 'female', language: 'ru', timezone: 'Europe/Kyiv', birth_date: '1998-04-02',
  avatar_media_id: null, prompt: 'Ты Марина.', is_active: true, appearance: '', voice_ambience_enabled: true,
  voice_id: 'Vindemiatrix', voice_style: '', voice_speed: 1, ai_enabled: true, voice_enabled: true, photo_enabled: true,
  video_enabled: true, calls_enabled: false, call_recording: true, call_recording_days: 90, reply_delay_s: 8,
};

const VOICES: Voice[] = [
  { id: 'Vindemiatrix', label: 'Vindemiatrix', gender: 'female', tone: 'нежный' },
  { id: 'Puck', label: 'Puck', gender: 'male', tone: 'бодрый' },
];

afterEach(() => setCsrfToken(null));

describe('toPatch', () => {
  it('returns only changed fields in API types', () => {
    const initial = toFormValues(PERSONA);
    const patch = toPatch({ ...initial, birth_date: '', reply_delay_s: '12', name: ' Марина ', voice_speed: 1.25 }, initial);
    expect(patch).toEqual({ birth_date: null, reply_delay_s: 12, name: 'Марина', voice_speed: 1.25 });
    expect(toPatch(initial, initial)).toEqual({});
  });

  it('validates every field with the schema, first problem per field', () => {
    const values = toFormValues(PERSONA);
    expect(personaFormErrors(values)).toEqual({});
    expect(personaFormErrors({ ...values, name: ' ', reply_delay_s: '1.5', birth_date: '2026-13', voice_speed: 2 })).toEqual({
      name: 'Укажите имя', reply_delay_s: 'Целое число от 0 до 120', birth_date: 'Дата вида 1999-12-31',
      voice_speed: 'От 0.8 до 1.5',
    });
  });

  it('sends the recording period as a number and checks its range', () => {
    const initial = toFormValues(PERSONA);
    expect(toPatch({ ...initial, call_recording: false, call_recording_days: '30' }, initial))
      .toEqual({ call_recording: false, call_recording_days: 30 });
    expect(personaFormErrors({ ...initial, call_recording_days: '0' })).toEqual({ call_recording_days: 'Целое число от 1 до 3650' });
    expect(personaFormErrors({ ...initial, call_recording_days: '' })).toEqual({ call_recording_days: 'Целое число от 1 до 3650' });
    expect(toFormValues({ ...PERSONA, call_recording_days: undefined } as unknown as Persona).call_recording_days).toBe('90');
  });

  it('says «older than N days» in Russian', () => {
    expect(olderThanDays(90)).toBe('Старше 90 дней');
    expect(olderThanDays(1)).toBe('Старше 1 дня');
    expect(olderThanDays(21)).toBe('Старше 21 дня');
    expect(olderThanDays(11)).toBe('Старше 11 дней');
  });

  it('has no agent or guard fields: they are edited in «Агенты»', () => {
    const keys = Object.keys(toFormValues(PERSONA));
    expect(keys.some((k) => k.startsWith('model_'))).toBe(false);
    expect(keys).not.toContain('guard_rules');
  });
});

describe('PersonaEditor', () => {
  it('tracks unsaved changes and saves only the changed fields', async () => {
    setCsrfToken('csrf-1');
    const log = fakeApi({
      'PUT /api/personas/1': (req: FakeRequest) => ({ ...PERSONA, ...(req.body as object) }),
    });
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={VOICES} />);

    const save = await screen.findByRole('button', { name: 'Сохранить' });
    expect(screen.getByText('Настройки персонажа сохранены')).toBeInTheDocument();
    expect(save).toBeDisabled();

    const name = screen.getByLabelText('Имя');
    await userEvent.clear(name);
    await userEvent.type(name, 'Марина К');
    await userEvent.click(screen.getByRole('switch', { name: 'Видео' }));
    expect(screen.getByText('Настройки персонажа изменены, не сохранены')).toBeInTheDocument();
    expect(save).toBeEnabled();

    await userEvent.click(save);
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    const put = log.find((r) => r.method === 'PUT');
    expect(put?.body).toEqual({ name: 'Марина К', video_enabled: false });
    expect(put?.headers['X-CSRF-Token']).toBe('csrf-1');
    expect(await screen.findByText('Настройки персонажа сохранены')).toBeInTheDocument();
  });

  it('takes a fresh persona while untouched and keeps an edit (sending only it) when the server changes', async () => {
    const log = fakeApi({ 'PUT /api/personas/1': (req: FakeRequest) => ({ ...PERSONA, ...(req.body as object) }) });
    function Live() {
      const [persona, setPersona] = useState(PERSONA);
      return (
        <>
          <button type="button" onClick={() => setPersona((p) => ({ ...p, prompt: `${p.prompt} Новое.` }))}>с сервера</button>
          <PersonaEditor persona={persona} voices={[]} />
        </>
      );
    }
    await renderWithApp(<Live />);
    const prompt = await screen.findByRole('textbox', { name: 'Промт' });
    await userEvent.click(screen.getByRole('button', { name: 'с сервера' }));
    expect(prompt).toHaveValue('Ты Марина. Новое.');
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();

    const name = screen.getByLabelText('Имя');
    await userEvent.type(name, '!');
    await userEvent.click(screen.getByRole('button', { name: 'с сервера' }));
    expect(name).toHaveValue('Марина!');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PUT')?.body).toEqual({ name: 'Марина!' });
  });

  it('keeps its save bar inside its own block: the sections below are not part of it', async () => {
    fakeApi({});
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={[]} />);
    const bar = await screen.findByRole('region', { name: 'Сохранение: настройки персонажа' });
    const form = document.getElementById(PERSONA_FORM_ID);
    expect(form?.parentElement).toBe(bar.parentElement);
  });

  it('keeps the saved voice when the voice list failed to load', async () => {
    fakeApi({});
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={[]} />);
    expect(await screen.findByText('Vindemiatrix')).toBeInTheDocument();
    expect(screen.getByText('Список голосов не загрузился')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Модели' })).not.toBeInTheDocument();
  });

  it('saves the chosen voice and the speech speed with the form', async () => {
    const log = fakeApi({ 'PUT /api/personas/1': (req: FakeRequest) => ({ ...PERSONA, ...(req.body as object) }) });
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={VOICES} />);
    expect(await screen.findByText('Vindemiatrix — нежный, женский')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Vindemiatrix/ })).toHaveAttribute('aria-checked', 'true');

    await userEvent.click(screen.getByRole('radio', { name: /Puck/ }));
    expect(screen.getByText('Puck — бодрый, мужской')).toBeInTheDocument();
    const speed = screen.getByLabelText('Скорость речи');
    expect(screen.getByText('×1.00')).toBeInTheDocument();
    fireEvent.change(speed, { target: { value: '1.3' } });
    expect(screen.getByText('×1.30')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PUT')?.body).toEqual({ voice_id: 'Puck', voice_speed: 1.3 });
  });

  it('saves «Записывать звонки» and the keeping period with the form', async () => {
    const log = fakeApi({ 'PUT /api/personas/1': (req: FakeRequest) => ({ ...PERSONA, ...(req.body as object) }) });
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={[]} />);
    await userEvent.click(await screen.findByRole('switch', { name: 'Записывать звонки' }));
    const days = screen.getByLabelText('Хранить записи, дней');
    await userEvent.clear(days);
    await userEvent.type(days, '30');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    expect(log.find((r) => r.method === 'PUT')?.body).toEqual({ call_recording: false, call_recording_days: 30 });
  });

  it('«Удалить старые записи» asks: older than her period, or every recording', async () => {
    const log = fakeApi({ 'POST /api/personas/1/call-recordings/purge': (req: FakeRequest) => ({ deleted: (req.body as { days: number | null }).days === 0 ? 12 : 3 }) });
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={[]} />);

    await userEvent.click(await screen.findByRole('button', { name: 'Удалить старые записи' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Удалить записи звонков?' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Старше 90 дней' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(1));
    expect(log[0]).toMatchObject({ path: '/api/personas/1/call-recordings/purge', body: { days: null } });
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Удалить старые записи' }));
    await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Все записи' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(2));
    expect(log[1]?.body).toEqual({ days: 0 });
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    // Purging is not a form change: nothing to save.
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('blocks saving an invalid form', async () => {
    const log = fakeApi({});
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={[]} />);
    const delay = await screen.findByLabelText('Пауза перед ответом, секунд');
    await userEvent.clear(delay);
    await userEvent.type(delay, '500');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Целое число от 0 до 120')).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'PUT')).toHaveLength(0);
  });
});
