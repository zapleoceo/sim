import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { setCsrfToken } from '@/lib/api';
import type { Persona } from '@/lib/types';
import { fakeApi, type FakeRequest } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { PERSONA_FORM_ID, PersonaEditor } from './persona-editor';
import { personaFormErrors, toFormValues, toPatch } from './persona-form-logic';

const PERSONA: Persona = {
  id: 1, name: 'Марина', gender: 'female', language: 'ru', timezone: 'Europe/Kyiv', birth_date: '1998-04-02',
  avatar_media_id: null, prompt: 'Ты Марина.', is_active: true, appearance: '', voice_ambience_enabled: true,
  voice_id: 'Vindemiatrix', voice_style: '', ai_enabled: true, voice_enabled: true, photo_enabled: true,
  video_enabled: true, calls_enabled: false, reply_delay_s: 8,
};

afterEach(() => setCsrfToken(null));

describe('toPatch', () => {
  it('returns only changed fields in API types', () => {
    const initial = toFormValues(PERSONA);
    const patch = toPatch({ ...initial, birth_date: '', reply_delay_s: '12', name: ' Марина ' }, initial);
    expect(patch).toEqual({ birth_date: null, reply_delay_s: 12, name: 'Марина' });
    expect(toPatch(initial, initial)).toEqual({});
  });

  it('validates every field with the schema, first problem per field', () => {
    const values = toFormValues(PERSONA);
    expect(personaFormErrors(values)).toEqual({});
    expect(personaFormErrors({ ...values, name: ' ', reply_delay_s: '1.5', birth_date: '2026-13' })).toEqual({
      name: 'Укажите имя', reply_delay_s: 'Целое число от 0 до 120', birth_date: 'Дата вида 1999-12-31',
    });
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
    await renderWithApp(<PersonaEditor persona={PERSONA} voices={[{ id: 'Vindemiatrix', label: 'Vindemiatrix' }]} />);

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
    const prompt = await screen.findByLabelText('Промт');
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
    const voice = await screen.findByLabelText<HTMLSelectElement>('Голос', { selector: 'select' });
    expect(voice.value).toBe('Vindemiatrix');
    expect(screen.queryByRole('heading', { name: 'Модели' })).not.toBeInTheDocument();
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
