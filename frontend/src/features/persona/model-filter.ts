import type { BrokerModel, ModelMatch } from '@/lib/types';

const has = (list: readonly string[] | null | undefined, value: string): boolean =>
  (list ?? []).some((v) => v.toLowerCase() === value.toLowerCase());

const hasAll = (list: readonly string[] | null | undefined, wanted: readonly string[]): boolean =>
  wanted.every((value) => has(list, value));

/** Does the Broker model fit an agent: all modalities and capabilities of ANY of its matches? */
export function matchesFilter(model: BrokerModel, filter: readonly ModelMatch[]): boolean {
  return filter.some(
    (m) =>
      hasAll(model.input_modalities, m.input) &&
      hasAll(model.output_modalities, m.output) &&
      hasAll(model.capabilities, m.capabilities),
  );
}

export interface ModelOption {
  value: string;
  label: string;
  available: boolean | null;
  /** The saved value that is not in the filtered list (kept so saving never drops it). */
  outsideFilter: boolean;
}

export function availabilityLabel(available: boolean | null): string {
  if (available === false) return 'недоступна';
  if (available === null) return 'доступность неизвестна';
  return 'доступна';
}

/**
 * Options of one select: models that fit the agent (available first, then by id), plus the
 * current value when it does not fit or is gone from the catalog.
 */
export function modelOptions(models: readonly BrokerModel[], filter: readonly ModelMatch[], current: string): ModelOption[] {
  const fit = models
    .filter((m) => matchesFilter(m, filter))
    .sort((a, b) => Number(b.available === true) - Number(a.available === true) || a.id.localeCompare(b.id));
  const options: ModelOption[] = fit.map((m) => ({
    value: m.id,
    label: m.available === true ? m.id : `${m.id} — ${availabilityLabel(m.available)}`,
    available: m.available,
    outsideFilter: false,
  }));
  if (current && !fit.some((m) => m.id === current)) {
    const known = models.find((m) => m.id === current);
    options.unshift({
      value: current,
      label: known ? `${current} — не подходит по возможностям` : `${current} — нет в каталоге Broker`,
      available: known ? known.available : null,
      outsideFilter: true,
    });
  }
  return options;
}
