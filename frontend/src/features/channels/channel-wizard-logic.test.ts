import {
  EMPTY_CONNECT, connectBody, nextStep, pollingDone, prefillConnect, validateConnect, verifyValue,
} from './channel-wizard-logic';

describe('channel wizard step machine', () => {
  it('goes to done when active', () => {
    expect(nextStep({ status: 'active' }, 'telegram', 'connect')).toBe('done');
  });

  it('asks for a password on telegram_user 2FA, otherwise for a code', () => {
    expect(nextStep({ status: 'challenge', need: '2fa' }, 'telegram_user', 'code')).toBe('password');
    expect(nextStep({ status: 'challenge', need: '2fa' }, 'instagram', 'connect')).toBe('code');
    expect(nextStep({ status: 'pending', need: 'code' }, 'telegram_user', 'connect')).toBe('code');
    expect(nextStep({ status: 'challenge', need: 'challenge' }, 'instagram', 'connect')).toBe('code');
  });

  it('shows the QR for whatsapp while pending, even before the image arrives', () => {
    expect(nextStep({ status: 'pending', qr_data_uri: 'data:x' }, 'whatsapp', 'connect')).toBe('qr');
    expect(nextStep({ status: 'pending' }, 'whatsapp', 'connect')).toBe('qr');
  });

  it('returns to the credentials step on error or expiry and stays put otherwise', () => {
    expect(nextStep({ status: 'error', message: 'x' }, 'instagram', 'code')).toBe('connect');
    expect(nextStep({ status: 'expired' }, 'whatsapp', 'qr')).toBe('connect');
    expect(nextStep({ status: 'pending' }, 'telegram', 'connect')).toBe('connect');
  });

  it('stops polling on terminal states only', () => {
    expect(pollingDone({ status: 'pending' })).toBe(false);
    expect(pollingDone({ status: 'active' })).toBe(true);
    expect(pollingDone({ status: 'error' })).toBe(true);
  });
});

describe('channel credentials', () => {
  it('validates the bot token format', () => {
    expect(validateConnect('telegram', { ...EMPTY_CONNECT, bot_token: '123' }).bot_token).toMatch(/123456:ABC/);
    expect(validateConnect('telegram', { ...EMPTY_CONNECT, bot_token: '123456:ABCDEFGHIJKLMNOPQRSTUV_-x' })).toEqual({});
  });

  it('validates telegram_user fields and optional proxy', () => {
    const errors = validateConnect('telegram_user', { ...EMPTY_CONNECT, method: 'code', api_id: '12a', api_hash: 'zz', phone: '1', proxy: 'bad' });
    expect(Object.keys(errors).sort()).toEqual(['api_hash', 'api_id', 'phone', 'proxy']);
    const ok = { ...EMPTY_CONNECT, api_id: '12345', api_hash: 'a'.repeat(32), phone: '+380 50 123 45 67', proxy: 'socks5://u:p@h:1080' };
    expect(validateConnect('telegram_user', ok)).toEqual({});
  });

  it('builds request bodies like the backend expects', () => {
    const v = { ...EMPTY_CONNECT, method: 'code', api_id: ' 123 ', api_hash: ` ${'b'.repeat(32)} `, phone: ' +380501234567 ', password: ' p ', username: ' me ' };
    expect(connectBody('telegram_user', v)).toEqual({ api_id: '123', api_hash: 'b'.repeat(32), phone: '+380501234567', method: 'code' });
    expect(connectBody('telegram_user', { ...v, method: 'qr' })).toEqual({ api_id: '123', api_hash: 'b'.repeat(32), method: 'qr' });
    expect(connectBody('instagram', { ...v, proxy: ' http://h:8080 ' })).toEqual({ username: 'me', password: ' p ', proxy: 'http://h:8080' });
    expect(connectBody('whatsapp', v)).toEqual({});
  });

  it('keeps the 2FA password as typed and trims codes', () => {
    expect(verifyValue('password', ' secret ')).toBe(' secret ');
    expect(verifyValue('code', ' 12345 ')).toBe('12345');
    expect(verifyValue('code', '12')).toBe('');
  });
});

describe('saved credentials', () => {
  it('allows empty fields when the last connect saved them, but checks what was typed', () => {
    expect(validateConnect('telegram_user', EMPTY_CONNECT)).toHaveProperty('api_id');
    expect(validateConnect('telegram_user', EMPTY_CONNECT, true)).toEqual({});
    expect(validateConnect('telegram_user', { ...EMPTY_CONNECT, method: 'code', phone: '12' }, true)).toEqual({ phone: 'Формат: +380501234567' });
    expect(validateConnect('telegram', EMPTY_CONNECT, true)).toEqual({});
  });
});

describe('prefill', () => {
  it('maps saved credentials onto the form and ignores unknown keys', () => {
    expect(prefillConnect({ api_id: '1', phone: '+1', secret: 'x' })).toEqual({ ...EMPTY_CONNECT, api_id: '1', phone: '+1' });
  });
});
