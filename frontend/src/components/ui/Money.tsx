import clsx from 'clsx';

export interface MoneyValue {
  amount: string | number;
  currency: string;
}

const LOCALES: Record<string, string> = { INR: 'en-IN' };

/**
 * Format a money amount. Accepts decimal strings ("420000.00") as returned by the API.
 * INR uses Indian digit grouping: 420000.00 -> ₹4,20,000.00. Invalid amounts give an em dash; null/undefined gives "Not available".
 */
// eslint-disable-next-line react-refresh/only-export-components -- formatter shared with Money component; pure function
export function formatMoney(value: MoneyValue | null | undefined): string {
  if (!value) return 'Not available';
  const { amount, currency } = value;
  const n = typeof amount === 'number' ? amount : Number(amount);
  if (!Number.isFinite(n)) return '—';
  const code = currency.toUpperCase();
  return new Intl.NumberFormat(LOCALES[code] ?? 'en-US', {
    style: 'currency',
    currency: code,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

export interface MoneyProps {
  /** Pass `value` (may be null -> "Not available") or amount + currency. */
  value?: MoneyValue | null;
  amount?: string | number;
  currency?: string;
  className?: string;
}

export function Money({ value, amount, currency, className }: MoneyProps) {
  const v = value !== undefined ? value : amount !== undefined && currency ? { amount, currency } : null;
  return <span className={clsx('tabular-nums', className)}>{formatMoney(v)}</span>;
}
