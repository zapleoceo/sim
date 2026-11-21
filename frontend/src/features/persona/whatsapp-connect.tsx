import { useMutation } from '@tanstack/react-query';
import { Loader2, QrCode } from 'lucide-react';
import { useState } from 'react';
import { Field } from '@/components/field';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { channelApi } from './api';
import { LoginShell, QrStep, useChannelLogin, type ConnectProps } from './channel-login';

/**
 * WhatsApp as a linked device: «Показать QR-код» → scan it in WhatsApp → Связанные устройства (the
 * QR is re-read until the phone links) → connected. «Отменить подключение» unlinks the device.
 */
export function WhatsAppConnect(props: ConnectProps) {
  const login = useChannelLogin(props);
  const { personaId, step, setStep, state } = login;
  const [phone, setPhone] = useState('');
  const start = useMutation({
    mutationFn: () => channelApi.startWhatsApp(personaId, { phone: phone.trim() }),
    onSuccess: setStep,
  });

  return (
    <LoginShell label="Подключение WhatsApp" step={step} onRetry={() => setStep(null)} onCancel={login.cancel}>
      {step === null && (
        <form
          noValidate
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            start.mutate();
          }}
        >
          <p className="text-sm text-muted-foreground">
            Персонаж станет «связанным устройством» номера WhatsApp, как WhatsApp Web. Телефон с этим номером должен
            заходить в сеть хотя бы раз в 14 дней, иначе WhatsApp отвяжет устройство.
          </p>
          <Field id="wa-phone" label="Номер (необязательно, для подписи)" className="sm:max-w-xs">
            <Input id="wa-phone" inputMode="tel" autoComplete="off" placeholder="+7…" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={props.onClose}>Отмена</Button>
            <Button type="submit" disabled={start.isPending}>
              {start.isPending ? <Loader2 className="animate-spin" /> : <QrCode />}
              Показать QR-код
            </Button>
          </div>
        </form>
      )}

      {state === 'qr' && step && <QrStep step={step} alt="QR-код для входа в WhatsApp" />}
    </LoginShell>
  );
}
