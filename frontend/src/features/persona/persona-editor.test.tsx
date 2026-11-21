import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { setCsrfToken } from '@/lib/api';
import type { BrokerModel, Persona } from '@/lib/types';
import { fakeApi, type FakeRequest } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { PersonaEditor } from './persona-editor';
import { toFormValues, toPatch } from './persona-form-logic';

const PERSONA: Persona = {
  id: 1, name: 'Марина', gender: 'female', language: 'ru', timezone: 'Europe/Kyiv', birth_date: '1998-04-02',
  avatar_media_id: null, prompt: 'Ты Марина.', model_reply: 'gpt-text', model_guard: 'gpt-text', model_vision: '',
  model_transcribe: '', model_memory: '', model_tts: '', model_image: '', model_video: 'retired-video', model_live: '',
  model_life: '', appearance: '', voice_ambience_enabled: true,
  voice_id: 'Vindemiatrix', voice_style: '', guard_rules: '', ai_enabled: true, voice_enabled: true, photo_enabled: true,
  video_enabled: true, calls_enabled: false, reply_delay_s: 8,
};

const MODELS: BrokerModel[] = [
  { id: 'gpt-text', available: true, input_modalities: ['text'], output_modalities: ['text'], capabilities: [] },
  { id: 'veo', available: false, input_modalities: ['text'], output_modalities: ['video'], capabilities: [] },
];

afterEach(() => setCsrfToken(null));

describe('toPatch', () => {
  it('returns only changed fields in API types', () => {
    const initial = toFormValues(PERSONA);
    const patch = toPatch({ ...initial, birth_date: '', reply_delay_s: '12', name: ' Марина ' }, initial);
    expect(patch).toEqual({ birth_date: null, reply_delay_s: 12, name: 'Марина' });
    expect(toPatch(initial, initial)).toEqual({});
  });
});

describe('PersonaEditor', () => {
  it('tracks unsaved changes and saves only the changed fields', async () => {
    setCsrfToken('csrf-1');
    const log = fakeApi({
      'PUT /api/persona': (req: FakeRequest) => ({ ...PERSONA, ...(req.body as object) }),
    });
    await renderWithApp(<PersonaEditor persona={PERSONA} models={MODELS} voices={[{ id: 'Vindemiatrix', label: 'Vindemiatrix' }]} />);

    const save = await screen.findByRole('button', { name: 'Сохранить' });
    expect(screen.getByText('Все изменения сохранены')).toBeInTheDocument();
    expect(save).toBeDisabled();

    const name = screen.getByLabelText('Имя');
    await userEvent.clear(name);
    await userEvent.type(name, 'Марина К');
    await userEvent.click(screen.getByRole('switch', { name: 'Видео' }));
    expect(screen.getByText('Есть несохранённые изменения')).toBeInTheDocument();
    expect(save).toBeEnabled();

    await userEvent.click(save);
    await waitFor(() => expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1));
    const put = log.find((r) => r.method === 'PUT');
    expect(put?.body).toEqual({ name: 'Марина К', video_enabled: false });
    expect(put?.headers['X-CSRF-Token']).toBe('csrf-1');
    expect(await screen.findByText('Все изменения сохранены')).toBeInTheDocument();
  });

  it('keeps a saved model that left the catalog and shows availability', async () => {
    fakeApi({});
    await renderWithApp(<PersonaEditor persona={PERSONA} models={MODELS} voices={[]} />);
    const video = await screen.findByLabelText<HTMLSelectElement>('Видео', { selector: 'select' });
    expect(video.value).toBe('retired-video');
    expect(screen.getByRole('option', { name: 'retired-video — нет в каталоге Broker' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'veo — недоступна' })).toBeInTheDocument();
    // The voice list failed to load, the saved voice is still selected.
    expect(screen.getByLabelText<HTMLSelectElement>('Голос', { selector: 'select' }).value).toBe('Vindemiatrix');
  });

  it('blocks saving an invalid form', async () => {
    const log = fakeApi({});
    await renderWithApp(<PersonaEditor persona={PERSONA} models={MODELS} voices={[]} />);
    const delay = await screen.findByLabelText('Пауза перед ответом, секунд');
    await userEvent.clear(delay);
    await userEvent.type(delay, '500');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Целое число от 0 до 120')).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'PUT')).toHaveLength(0);
  });
});
