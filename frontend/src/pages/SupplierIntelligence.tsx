import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { listSuppliers } from '@/api/endpoints';
import { queryKeys } from '@/api/queries';
import { ApiError } from '@/api/client';
import { Button, Card, EmptyState, ErrorState, Field, Input, Select } from '@/components/ui';
import { SupplierTable } from '@/features/suppliers/list/SupplierTable';
import { RISK_OPTIONS, filtersFromParams } from '@/features/suppliers/list/params';
import { STATUS_OPTIONS } from '@/features/suppliers/status';

const DEBOUNCE_MS = 300;

export default function SupplierIntelligence() {
  const [params, setParams] = useSearchParams();
  const filters = filtersFromParams(params);
  const pageSize = filters.page_size ?? 10;

  const [searchText, setSearchText] = useState(params.get('search') ?? '');
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  /** Update params; any filter change resets to page 1 unless keepPage. */
  const update = (changes: Record<string, string | undefined>, keepPage = false) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(changes)) {
          if (v) next.set(k, v);
          else next.delete(k);
        }
        if (!keepPage) next.delete('page');
        return next;
      },
      { replace: true },
    );

  const onSearch = (value: string) => {
    setSearchText(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => update({ search: value.trim() || undefined }), DEBOUNCE_MS);
  };

  const clear = () => {
    clearTimeout(timer.current);
    setSearchText('');
    update({ search: undefined, status: undefined, risk_level: undefined });
  };

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useQuery({
    queryKey: queryKeys.suppliers(filters),
    queryFn: () => listSuppliers(filters),
    placeholderData: keepPreviousData,
  });

  const totalCount = data?.count ?? 0;
  const outOfRange = !!data && !isPlaceholderData && data.results.length === 0 && totalCount > 0 && filters.page > 1;
  const lastPage = Math.max(1, Math.ceil(totalCount / pageSize));
  // ?page=N beyond the last page: clamp to the last page (replace, no history entry).
  useEffect(() => {
    if (outOfRange) update({ page: String(lastPage) }, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outOfRange, lastPage]);

  const hasFilters = !!(filters.search || filters.status || filters.risk_level || searchText);
  const totalPages = data ? Math.max(1, Math.ceil(data.count / pageSize)) : 1;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-semibold text-text">Supplier Intelligence</h1>
        {data && (
          <p className="text-sm text-muted" aria-live="polite">
            {data.count} {data.count === 1 ? 'supplier' : 'suppliers'}
          </p>
        )}
      </header>

      <form
        role="search"
        aria-label="Supplier filters"
        onSubmit={(e) => e.preventDefault()}
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_auto] lg:items-end"
      >
        <Field label="Search">
          <Input
            type="search"
            placeholder="Name or ID"
            value={searchText}
            onChange={(e) => onSearch(e.target.value)}
          />
        </Field>
        <Field label="Status">
          <Select value={filters.status ?? ''} onChange={(e) => update({ status: e.target.value || undefined })}>
            <option value="">All</option>
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Risk level">
          <Select value={filters.risk_level ?? ''} onChange={(e) => update({ risk_level: e.target.value || undefined })}>
            <option value="">All</option>
            {RISK_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Button variant="secondary" onClick={clear} disabled={!hasFilters}>
          Clear filters
        </Button>
      </form>

      <Card className="overflow-hidden">
        {isError ? (
          <ErrorState
            message={error instanceof ApiError ? error.message : 'Could not load suppliers.'}
            correlationId={error instanceof ApiError ? (error.correlationId ?? undefined) : undefined}
            onRetry={() => void refetch()}
          />
        ) : isPending ? (
          <SupplierTable loading />
        ) : data.results.length === 0 ? (
          <EmptyState
            title="No suppliers match these filters"
            description="Try a different search or clear the filters."
            action={
              <Button variant="secondary" size="sm" onClick={clear}>
                Clear
              </Button>
            }
          />
        ) : (
          <div className={isPlaceholderData ? 'opacity-60 transition-opacity' : undefined}>
            <SupplierTable suppliers={data.results} />
          </div>
        )}

        {data && data.count > 0 && (
          <nav aria-label="Pagination" className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
            <Button
              variant="secondary"
              size="sm"
              disabled={!data.previous}
              onClick={() => update({ page: String(filters.page - 1) }, true)}
            >
              Prev
            </Button>
            <span className="text-sm text-muted">
              Page {filters.page} of {totalPages}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={!data.next}
              onClick={() => update({ page: String(filters.page + 1) }, true)}
            >
              Next
            </Button>
          </nav>
        )}
      </Card>
    </div>
  );
}
