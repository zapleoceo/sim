import {
  DEFAULT_SORT, EMPTY_LOG_FILTERS, activeFilterCount, detailPairs, formatMs, nextSort, timelineBar, toLogParams,
  toggle, validateLogFilters,
} from './log-logic';

const PAGE = { page: 2, size: 50 };

describe('system log logic', () => {
  it('sorts a new column descending and flips the same one', () => {
    expect(nextSort(DEFAULT_SORT, 'duration_ms')).toEqual({ field: 'duration_ms', order: 'desc' });
    expect(nextSort({ field: 'duration_ms', order: 'desc' }, 'duration_ms')).toEqual({ field: 'duration_ms', order: 'asc' });
    expect(nextSort({ field: 'duration_ms', order: 'asc' }, 'duration_ms').order).toBe('desc');
  });

  it('serializes every column filter, sort and page', () => {
    const params = toLogParams(
      {
        ...EMPTY_LOG_FILTERS, q: ' timeout ', kinds: ['llm', 'guard'], statuses: ['error'], name: ' reply ',
        trace_id: 'ABC', min_ms: '100', max_ms: '900', min_attempt: '2', http_status: '500', channel_id: 3,
        errors_only: true, from: '2026-11-10T12:00',
      },
      { field: 'duration_ms', order: 'asc' },
      PAGE,
    );
    expect(params).toMatchObject({
      q: 'timeout', kind: 'llm,guard', status: 'error', name: 'reply', trace_id: 'abc', min_ms: 100, max_ms: 900,
      min_attempt: 2, http_status: 500, channel_id: 3, has_error: 'true', sort: 'duration_ms', order: 'asc',
      page: 2, size: 50, to: null,
    });
    expect(typeof params?.from).toBe('string');
    expect(toLogParams(EMPTY_LOG_FILTERS, DEFAULT_SORT, PAGE)).toMatchObject({ kind: '', has_error: null, min_ms: null });
  });

  it('refuses invalid filters instead of sending them', () => {
    expect(validateLogFilters({ ...EMPTY_LOG_FILTERS, min_ms: '1.5' }).min_ms).toBeTruthy();
    expect(validateLogFilters({ ...EMPTY_LOG_FILTERS, min_ms: '9', max_ms: '1' }).range).toBeTruthy();
    expect(validateLogFilters({ ...EMPTY_LOG_FILTERS, min_attempt: '0' }).min_attempt).toBeTruthy();
    expect(validateLogFilters({ ...EMPTY_LOG_FILTERS, http_status: '99' }).http_status).toBeTruthy();
    expect(validateLogFilters({ ...EMPTY_LOG_FILTERS, from: '2026-11-10T12:00', to: '2026-11-10T11:00' }).dates).toBeTruthy();
    expect(toLogParams({ ...EMPTY_LOG_FILTERS, max_ms: 'abc' }, DEFAULT_SORT, PAGE)).toBeNull();
    expect(validateLogFilters(EMPTY_LOG_FILTERS)).toEqual({});
  });

  it('counts active filters and toggles choices', () => {
    expect(activeFilterCount(EMPTY_LOG_FILTERS)).toBe(0);
    expect(activeFilterCount({ ...EMPTY_LOG_FILTERS, kinds: ['job'], min_ms: '5', max_ms: '9', errors_only: true })).toBe(3);
    expect(toggle(['a', 'b'], 'a')).toEqual(['b']);
    expect(toggle(['a'], 'b')).toEqual(['a', 'b']);
  });

  it('formats durations and timeline bars', () => {
    expect(formatMs(null)).toBe('—');
    expect(formatMs(850)).toBe('850 мс');
    expect(formatMs(1250)).toBe('1,3 с');
    expect(formatMs(15_000)).toBe('15 с');
    expect(formatMs(125_000)).toBe('2 мин 5 с');
    expect(timelineBar(500, 500, 1000)).toEqual({ left: 50, width: 50 });
    expect(timelineBar(0, 0, 0).width).toBeGreaterThan(0);
    expect(timelineBar(1000, 10, 1000).left).toBeLessThan(100);
  });

  it('labels step details and skips empty values', () => {
    expect(detailPairs({ queue_ms: 1500, gave_up: true, outcome: 'sent', guard: null, nested: { a: 1 } })).toEqual([
      ['Ожидание в очереди', '1,5 с'],
      ['Передано оператору', 'да'],
      ['Итог', 'sent'],
    ]);
  });
});
