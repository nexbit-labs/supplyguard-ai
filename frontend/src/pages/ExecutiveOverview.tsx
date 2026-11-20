import { Link } from 'react-router-dom';
import { useDashboardSummary } from '@/api/queries';
import { ApiError } from '@/api/client';
import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  KpiTile,
  Money,
} from '@/components/ui';
import { Skeleton } from '@/components/ui/Spinner';
import { CasesTrendChart, ExposureTrendChart } from '@/features/overview/TrendCharts';
import { hasTrend } from '@/features/overview/format';

const KPI_LABELS = [
  'Suppliers',
  'Open POs',
  'Active shipments',
  'Exposure',
  'Open risk cases',
  'Recovered value',
] as const;

const GRID = 'grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3';

export default function ExecutiveOverview() {
  const { data, isPending, isError, error, refetch } = useDashboardSummary();
  const apiErr = error instanceof ApiError ? error : null;
  const empty = !!data && !hasTrend(data.exposure_trend) && !hasTrend(data.cases_trend);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-text">Executive Overview</h1>
        <p className="mt-1 text-sm text-muted">Supply-chain exposure, open risk cases and recovery at a glance.</p>
      </header>

      {isError ? (
        <Card>
          <ErrorState
            title="Could not load the overview"
            message={error instanceof Error ? error.message : 'Unexpected error.'}
            correlationId={apiErr?.correlationId ?? undefined}
            onRetry={() => void refetch()}
          />
        </Card>
      ) : (
        <>
          <section aria-label="Key metrics" className={GRID}>
            {isPending || !data ? (
              KPI_LABELS.map((l) => <KpiTile key={l} label={l} value={null} loading />)
            ) : (
              <>
                <KpiTile label="Suppliers" value={data.suppliers} />
                <KpiTile label="Open POs" value={data.open_pos} />
                <KpiTile label="Active shipments" value={data.active_shipments} />
                <KpiTile label="Exposure" value={<Money value={data.exposure} />} />
                <KpiTile
                  label="Open risk cases"
                  value={
                    <Link to="/risk" className="text-primary hover:underline">
                      {data.open_cases}
                    </Link>
                  }
                  hint="View risk cases"
                />
                <KpiTile label="Recovered value" value={<Money value={data.recovered_value} />} />
              </>
            )}
          </section>

          <section aria-label="Trends" className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Exposure trend</CardTitle>
              </CardHeader>
              <CardBody>
                {isPending || !data ? (
                  <div data-testid="chart-skeleton"><Skeleton className="h-64 w-full" /></div>
                ) : empty || !hasTrend(data.exposure_trend) ? (
                  <EmptyState title="No trend data yet" description="Exposure history will appear once disruptions are tracked." />
                ) : (
                  <ExposureTrendChart data={data.exposure_trend} />
                )}
              </CardBody>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Risk cases trend</CardTitle>
              </CardHeader>
              <CardBody>
                {isPending || !data ? (
                  <div data-testid="chart-skeleton"><Skeleton className="h-64 w-full" /></div>
                ) : empty || !hasTrend(data.cases_trend) ? (
                  <EmptyState title="No trend data yet" description="Case history will appear once risk cases are opened." />
                ) : (
                  <CasesTrendChart data={data.cases_trend} />
                )}
              </CardBody>
            </Card>
          </section>
        </>
      )}
    </div>
  );
}
