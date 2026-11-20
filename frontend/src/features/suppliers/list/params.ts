import type { RiskLevel, SupplierFilters } from '@/api/types';
import { STATUS_OPTIONS } from '../status';

export const RISK_OPTIONS: { value: RiskLevel; label: string }[] = [
  { value: 'LOW', label: 'Low' },
  { value: 'MEDIUM', label: 'Medium' },
  { value: 'HIGH', label: 'High' },
  { value: 'CRITICAL', label: 'Critical' },
];

const pick = <T extends string>(raw: string | null, options: { value: T }[]): T | undefined =>
  options.find((o) => o.value === raw)?.value;

const posInt = (raw: string | null): number | undefined => {
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : undefined;
};

/** Parse URL search params into API filters (invalid values are dropped). */
export function filtersFromParams(p: URLSearchParams): SupplierFilters & { page: number } {
  return {
    search: p.get('search')?.trim() || undefined,
    status: pick(p.get('status'), STATUS_OPTIONS),
    risk_level: pick(p.get('risk_level'), RISK_OPTIONS),
    page: posInt(p.get('page')) ?? 1,
    page_size: posInt(p.get('page_size')),
  };
}
