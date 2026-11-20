import { Link, useParams } from 'react-router-dom';
import { useRiskCase } from '@/api/queries';
import { ApiError } from '@/api/client';
import { ErrorState } from '@/components/ui';
import { AffectedPosTable } from '@/features/risk/detail/AffectedPosTable';
import { AnalysisSlot } from '@/features/risk/detail/AnalysisSlot';
import { ApprovalsExecutions } from '@/features/risk/detail/ApprovalsExecutions';
import { CaseDetailSkeleton } from '@/features/risk/detail/CaseDetailSkeleton';
import { CaseHeader } from '@/features/risk/detail/CaseHeader';
import { EventCard } from '@/features/risk/detail/EventCard';
import { RecommendationsSummary } from '@/features/risk/detail/RecommendationsSummary';
import { StatusTimeline } from '@/features/risk/detail/StatusTimeline';

export default function RiskCaseDetail() {
  const { id } = useParams<{ id: string }>();
  const q = useRiskCase(id);

  const crumb = (
    <nav aria-label="Breadcrumb" className="mb-4 text-sm text-muted">
      <Link className="hover:underline" to="/risk">
        Risk Command Center
      </Link>
      <span aria-hidden="true"> / </span>
      <span aria-current="page" className="text-text">
        {id}
      </span>
    </nav>
  );

  if (q.isPending) {
    return (
      <div className="min-w-0">
        {crumb}
        <CaseDetailSkeleton />
      </div>
    );
  }
  if (q.isError) {
    const err = q.error;
    const api = err instanceof ApiError ? err : null;
    if (api?.status === 404) {
      return (
        <div>
          {crumb}
          <ErrorState title="Risk case not found" message={`No risk case with id ${id} exists for your tenant.`} />
          <p className="text-center">
            <Link className="text-primary hover:underline" to="/risk">
              ← Back to Risk Command Center
            </Link>
          </p>
        </div>
      );
    }
    return (
      <div>
        {crumb}
        <ErrorState
          message={err.message || 'Could not load this risk case.'}
          correlationId={api?.correlationId ?? undefined}
          onRetry={() => void q.refetch()}
        />
      </div>
    );
  }

  const d = q.data;
  return (
    <div className="min-w-0 space-y-6">
      <div>
        {crumb}
        <CaseHeader detail={d} />
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          <EventCard event={d.event} />
          <AffectedPosTable pos={d.affected_pos} />
          <AnalysisSlot caseId={d.id} status={d.status} analysis={d.analysis} />
          <RecommendationsSummary detail={d} />
        </div>
        <div className="min-w-0 space-y-6">
          <StatusTimeline status={d.status} timeline={d.timeline} />
          <ApprovalsExecutions approvals={d.approvals} executions={d.executions} />
        </div>
      </div>
    </div>
  );
}
