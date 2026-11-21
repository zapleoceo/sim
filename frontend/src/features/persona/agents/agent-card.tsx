import { RotateCcw } from 'lucide-react';
import { type ReactNode, useState } from 'react';
import { Field } from '@/components/field';
import { Segmented } from '@/components/segmented';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import type { AgentRegistry, AgentSettings, AgentSpec, BrokerModel } from '@/lib/types';
import { cn } from '@/lib/utils';
import { availabilityLabel, modelOptions } from '../model-filter';
import {
  DEFAULT_MODEL, MODEL_OFF, canSwitchOff, defaultModelLabel, isSubscriptionModel, modelToSelect, promptText, promptValue,
  selectToModel,
} from './agents-logic';

const DOT: Record<string, string> = {
  true: 'bg-success',
  false: 'bg-destructive',
  null: 'bg-muted-foreground/50',
};

export interface AgentCardProps {
  spec: AgentSpec;
  specs: readonly AgentSpec[];
  settings: AgentSettings;
  /** The model this draft would use (own choice, default or fallback). */
  effective: string;
  models: readonly BrokerModel[];
  tiers: AgentRegistry['key_tiers'];
  tokensRange: AgentRegistry['max_tokens_range'];
  onChange: (settings: AgentSettings) => void;
  onReset: () => void;
  resetting?: boolean;
  /** Agent-specific extras (the guard's rule list). */
  children?: ReactNode;
}

/** What the effective model is: availability in the catalog, the subscription, or «off». */
function ModelStatus({ effective, models, outsideFilter }: { effective: string; models: readonly BrokerModel[]; outsideFilter: boolean }) {
  if (!effective) return <span className="text-warning">Модель не выбрана — агент не работает</span>;
  if (isSubscriptionModel(effective)) {
    return <span>{effective} — подписка: ключи Broker не тратятся, уровень ключей не важен</span>;
  }
  const known = models.find((m) => m.id === effective);
  const available = known ? known.available : null;
  return (
    <>
      <span className={cn('size-2 flex-none rounded-full', DOT[String(available)])} aria-hidden="true" />
      <span className={cn('truncate', outsideFilter && 'text-warning')}>
        {effective} — {outsideFilter ? 'не из списка подходящих, проверьте' : known ? availabilityLabel(available) : 'нет в каталоге Broker'}
      </span>
    </>
  );
}

/** One agent: title, what it does, model, key tier, prompt and max_tokens. */
export function AgentCard({
  spec, specs, settings, effective, models, tiers, tokensRange, onChange, onReset, resetting, children,
}: AgentCardProps) {
  const id = `agent-${spec.key}`;
  const options = modelOptions(models, spec.model_filter, settings.model ?? '');
  const outsideFilter = options.some((o) => o.outsideFilter && o.value === effective);
  const set = (patch: Partial<AgentSettings>) => onChange({ ...settings, ...patch });
  const tierOptions = tiers.map((t) => ({
    value: t.value,
    title: t.value === spec.key_tier ? `${t.label} — по умолчанию для этого агента` : t.label,
    label: (
      <>
        {t.label}
        {t.value === spec.key_tier && <span className="text-[10px] opacity-70">·&nbsp;станд.</span>}
      </>
    ),
  }));

  return (
    <article aria-labelledby={`${id}-title`} className="flex min-w-0 flex-col gap-3 rounded-lg border bg-background p-3 sm:p-4">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 id={`${id}-title`} className="font-medium">{spec.title}</h4>
          <p className="text-xs text-muted-foreground">{spec.description}</p>
        </div>
        <Button variant="ghost" size="icon-sm" disabled={resetting} onClick={onReset} aria-label={`${spec.title}: всё по умолчанию`} title="Всё по умолчанию">
          <RotateCcw />
        </Button>
      </header>

      <div className="flex min-w-0 flex-col gap-1.5">
        <label htmlFor={`${id}-model`} className="text-sm font-medium">Модель</label>
        <NativeSelect id={`${id}-model`} value={modelToSelect(settings.model)} onChange={(e) => set({ model: selectToModel(e.target.value) })}>
          <option value={DEFAULT_MODEL}>{defaultModelLabel(spec, specs)}</option>
          {canSwitchOff(spec) && <option value={MODEL_OFF}>Не использовать</option>}
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </NativeSelect>
        <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          <ModelStatus effective={effective} models={models} outsideFilter={outsideFilter} />
        </p>
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="text-sm font-medium">Ключи</span>
        <Segmented
          label={`${spec.title}: ключи`}
          value={settings.key_tier}
          options={tierOptions}
          onChange={(key_tier) => set({ key_tier })}
          className="w-full sm:w-fit"
        />
      </div>

      {spec.max_tokens !== null && (
        <Field id={`${id}-tokens`} label="max_tokens" hint={`По умолчанию ${spec.max_tokens}; пусто — по умолчанию.`} className="max-w-56">
          <Input
            id={`${id}-tokens`}
            type="number"
            inputMode="numeric"
            min={tokensRange.min}
            max={tokensRange.max}
            value={settings.max_tokens ?? ''}
            onChange={(e) => set({ max_tokens: e.target.value === '' ? null : Number(e.target.value) })}
          />
        </Field>
      )}

      {children}

      {spec.prompt !== null && (
        <details className="group rounded-md border px-3 py-2">
          <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium select-none">
            Промт
            {settings.prompt ? <Badge variant="warning">свой</Badge> : <Badge variant="muted">стандартный</Badge>}
          </summary>
          <div className="mt-2 flex flex-col gap-2">
            <PromptEditor
              id={`${id}-prompt`}
              label={`${spec.title}: промт`}
              settings={settings}
              spec={spec}
              onPrompt={(prompt) => set({ prompt })}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">
                {spec.placeholders.length > 0
                  ? `Код подставит: ${spec.placeholders.map((p) => `{${p}}`).join(', ')}.`
                  : 'Без подстановок.'}
              </p>
              <Button variant="outline" size="sm" disabled={!settings.prompt} onClick={() => set({ prompt: '' })}>
                Сбросить к стандартному
              </Button>
            </div>
          </div>
        </details>
      )}

      {spec.readonly_prompt && (
        <details className="rounded-md border px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium select-none">Правила в коде (только чтение)</summary>
          <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted/60 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
            {spec.readonly_prompt}
          </pre>
        </details>
      )}
    </article>
  );
}

interface PromptEditorProps {
  id: string;
  label: string;
  settings: AgentSettings;
  spec: AgentSpec;
  onPrompt: (prompt: string) => void;
}

/** The prompt textarea keeps the text exactly as typed (a trailing space survives while
 * typing); the stored value is normalized by `promptValue` (the default text = «by default»). */
function PromptEditor({ id, label, settings, spec, onPrompt }: PromptEditorProps) {
  const [text, setText] = useState(() => promptText(settings, spec));
  const [seen, setSeen] = useState(settings.prompt);
  if (seen !== settings.prompt) {
    // Reset or a reload changed the stored prompt from outside: show it, unless it is just
    // the normalized form of what is being typed.
    setSeen(settings.prompt);
    if (promptValue(text, spec) !== settings.prompt) setText(promptText(settings, spec));
  }
  return (
    <Textarea
      id={id}
      aria-label={label}
      className="max-h-[60vh] min-h-40 font-mono text-[13px]"
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onPrompt(promptValue(e.target.value, spec));
      }}
    />
  );
}
