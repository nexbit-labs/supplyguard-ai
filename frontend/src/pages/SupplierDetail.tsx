import { Link, useParams } from 'react-router-dom';
import { useSupplier } from '@/api/queries';
import { ApiError } from '@/api/client';
import type { SupplierDetail as SupplierDetailData } from '@/api/types';
import { Badge, Button, ErrorState, KpiTile, Money, SeverityBadge, Skeleton } from '@/components/ui';
import { DisruptionHistory } from '@/features/suppliers/detail/DisruptionHistory';
import { SUPPLIER_STATUS } from '@/features/suppliers/status';

function Breadcrumb({ name }: { name?: string }) {
  return (
    <nav aria-label="Breadcrumb" className="text-sm text-muted">
      <ol className="flex items-center gap-1">
        <li>
          <Link to="/suppliers" className="text-info hover:underline">
            Suppliers
          </Link>
        </li>
        {name && (
          <>
            <li aria-hidden="true">/</li>
            <li aria-current="page" className="text-text">
              {name}
            </li>
          </>
        )}
      </ol>
    </nav>
  );
}

function LoadingView() {
  return (
    <div aria-busy="true" data-testid="supplier-skeleton" className="space-y-4">
      <Skeleton className="h-8 w-72" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-48" />
    </div>
  );
}

const METER_TINT = {
  LOW: 'bg-sev-low',
  MEDIUM: 'bg-sev-medium',
  HIGH: 'bg-sev-high',
  CRITICAL: 'bg-sev-critical',
} as const;

function Header({ s }: { s: SupplierDetailData }) {
  const status = SUPPLIER_STATUS[s.status];
  const score = Math.max(0, Math.min(100, s.risk_score));
  return (
    <header className="space-y-2">
      <h1 className="text-2xl font-semibold">{s.name}</h1>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        <span className="font-mono text-muted">{s.id}</span>
        <span className="text-muted">
          {s.country} · {s.category}
        </span>
        <Badge tone={status.tone}>{status.label}</Badge>
        <SeverityBadge severity={s.risk_level} />
        <span className="flex items-center gap-2">
          <span
            role="meter"
            aria-label="Risk score"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={score}
            aria-valuetext={`${score} out of 100`}
            className="h-2 w-28 overflow-hidden rounded-full bg-surface-2"
          >
            <span className={`block h-full ${METER_TINT[s.risk_level]}`} style={{ width: `${score}%` }} />
          </span>
          <span className="tabular-nums text-muted">{score}/100</span>
        </span>
        <a className="text-info hover:underline" href={`mailto:${s.contact_email}`}>
          {s.contact_email}
        </a>
      </div>
    </header>
  );
}

export default function SupplierDetail() {
  const { id } = useParams<{ id: string }>();
  const q = useSupplier(id);

  if (q.isPending) {
    return (
      <section className="space-y-4">
        <Breadcrumb />
        <LoadingView />
      </section>
    );
  }

  if (q.isError) {
    const err = q.error;
    if (err instanceof ApiError && err.status === 404) {
      return (
        <section className="space-y-4">
          <Breadcrumb />
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
            <h1 className="text-xl font-semibold">Supplier not found</h1>
            <p className="text-sm text-muted">No supplier with ID {id} exists.</p>
            <Link to="/suppliers">
              <Button variant="secondary" size="sm" className="mt-2">
                Back to suppliers
              </Button>
            </Link>
          </div>
        </section>
      );
    }
    return (
      <section className="space-y-4">
        <Breadcrumb />
        <ErrorState
          title="Could not load supplier"
          message={err.message}
          correlationId={err instanceof ApiError ? (err.correlationId ?? undefined) : undefined}
          onRetry={() => void q.refetch()}
        />
      </section>
    );
  }

  const s = q.data;
  const k = s.kpis;
  return (
    <section className="space-y-6">
      <Breadcrumb name={s.name} />
      <Header s={s} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <KpiTile label="On-time delivery" value={`${k.on_time_delivery_pct}%`} />
        <KpiTile label="Quality pass" value={`${k.quality_pass_pct}%`} />
        <KpiTile label="Avg lead time" value={`${k.avg_lead_time_days} days`} />
        <KpiTile label="Open PO value" value={<Money value={k.open_po_value} />} />
        <KpiTile label="Disruptions (12 months)" value={k.disruptions_last_12m} />
      </div>
      <DisruptionHistory items={s.disruption_history} />
    </section>
  );
}
