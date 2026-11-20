import { compactInr, formatDateTime, shortDate } from './format';

describe('format helpers', () => {
  it('compactInr scales to K / L / Cr and trims decimals', () => {
    expect(compactInr(950)).toBe('₹950');
    expect(compactInr(12_000)).toBe('₹12K');
    expect(compactInr(4_500_000)).toBe('₹45L');
    expect(compactInr(25_000_000)).toBe('₹2.5Cr');
    expect(compactInr(-4_500_000)).toBe('₹-45L');
  });

  it('shortDate formats in UTC and passes invalid input through', () => {
    expect(shortDate('2026-11-30')).toBe('30 Nov');
    expect(shortDate('not-a-date')).toBe('not-a-date');
  });

  it('formatDateTime formats in UTC and passes invalid input through', () => {
    expect(formatDateTime('2026-11-19T08:05:00Z')).toMatch(/^19 Nov 2026,? 08:05 UTC$/);
    expect(formatDateTime('nope')).toBe('nope');
  });
});
