import clsx from 'clsx';
import { Select } from '@/components/ui';
import { STATUS_META } from '@/components/ui/StatusBadge';
import type { RiskCaseState, Severity } from '@/api/types';

import { SEVERITIES, STATES } from './filterOptions';

const cap = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();

interface Props {
  severity: Severity | undefined;
  status: RiskCaseState | undefined;
  onSeverity: (s: Severity | undefined) => void;
  onStatus: (s: RiskCaseState | undefined) => void;
}

export function CaseFilters({ severity, status, onSeverity, onStatus }: Props) {
  const options: (Severity | undefined)[] = [undefined, ...SEVERITIES];
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div role="group" aria-label="Severity" className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-sm font-medium text-text">Severity</span>
        {options.map((s) => {
          const active = s === severity;
          return (
            <button
              key={s ?? 'all'}
              type="button"
              aria-pressed={active}
              onClick={() => onSeverity(s)}
              className={clsx(
                'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                active
                  ? 'border-primary bg-primary text-primary-fg'
                  : 'border-border bg-surface text-text hover:bg-surface-2',
              )}
            >
              {s ? cap(s) : 'All'}
            </button>
          );
        })}
      </div>
      <div className="flex items-center gap-2">
        <label htmlFor="risk-status-filter" className="text-sm font-medium text-text">
          Status
        </label>
        <Select
          id="risk-status-filter"
          value={status ?? ''}
          onChange={(e) => onStatus((e.target.value || undefined) as RiskCaseState | undefined)}
          className="w-52"
        >
          <option value="">All statuses</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {STATUS_META[s].label}
            </option>
          ))}
        </Select>
      </div>
    </div>
  );
}
