import { useSearchParams } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { useRiskCases } from '@/api/queries';
import type { RiskCaseState, Severity } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Button, EmptyState, ErrorState, Skeleton } from '@/components/ui';
import { CaseFilters } from './CaseFilters';
import { SEVERITIES, STATES } from './filterOptions';
import { CaseTable } from './CaseTable';
import { clockTime } from './format';
import { Pagination } from './Pagination';
import { SimulateDisruption } from './SimulateDisruption';
import { useNewCases } from './useNewCases';

export const POLL_MS = 3000;
const PAGE_SIZE = 10;

export function RiskInbox() {
  const [params, setParams] = useSearchParams();
  const { hasRole } = useAuth();

  const severity = SEVERITIES.find((s) => s === params.get('severity'));
  const status = STATES.find((s) => s === params.get('status'));
  const page = Math.max(1, Number(params.get('page')) || 1);

  const setFilter = (key: 'severity' | 'status', value: Severity | RiskCaseState | undefined) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete('page'); // filter change resets paging
    setParams(next);
  };
  const setPage = (p: number) => {
    const next = new URLSearchParams(params);
    if (p > 1) next.set('page', String(p));
    else next.delete('page');
    setParams(next);
  };

  const { data, isPending, isError, error, refetch, isPlaceholderData, dataUpdatedAt } = useRiskCases(
    { severity, status, page, page_size: PAGE_SIZE },
    { refetchInterval: POLL_MS, keepPrevious: true },
  );

  const viewKey = `${severity ?? ''}|${status ?? ''}|${page}`;
  const { fresh, announcement } = useNewCases(
    data && !isPlaceholderData ? data.results.map((c) => c.id) : undefined,
    viewKey,
  );

  const filtered = !!(severity || status);
  const now = dataUpdatedAt; // relative times refresh with every poll

  let body;
  if (isPending) {
    body = (
      <div className="space-y-2" aria-busy="true" aria-label="Loading risk cases">
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-10" />
        ))}
      </div>
    );
  } else if (!data) {
    body = (
      <ErrorState
        title="Could not load risk cases"
        message={error instanceof Error ? error.message : 'Unknown error'}
        correlationId={error instanceof ApiError ? (error.correlationId ?? undefined) : undefined}
        onRetry={() => void refetch()}
      />
    );
  } else if (data.count === 0) {
    body = filtered ? (
      <EmptyState
        title="No cases match these filters"
        description="Try a different severity or status."
        action={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              const next = new URLSearchParams(params);
              ['severity', 'status', 'page'].forEach((k) => next.delete(k));
              setParams(next);
            }}
          >
            Clear filters
          </Button>
        }
      />
    ) : (
      <EmptyState title="No risk cases yet" description="Cases appear here when a supplier disruption is detected." />
    );
  } else {
    body = (
      <div className="space-y-3">
        <CaseTable items={data.results} fresh={fresh} now={now} />
        <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
      </div>
    );
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Risk Command Center</h1>
          <p className="mt-1 text-xs text-muted" data-testid="live-indicator">
            {isError && data ? (
              'Update failed - retrying'
            ) : (
              <>
                <span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-full bg-success align-middle" />
                Live · updated {dataUpdatedAt ? clockTime(dataUpdatedAt) : '--:--:--'}
              </>
            )}
          </p>
        </div>
        {hasRole('ADMIN') && <SimulateDisruption />}
      </div>

      <CaseFilters
        severity={severity}
        status={status}
        onSeverity={(s) => setFilter('severity', s)}
        onStatus={(s) => setFilter('status', s)}
      />

      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {body}
    </section>
  );
}
