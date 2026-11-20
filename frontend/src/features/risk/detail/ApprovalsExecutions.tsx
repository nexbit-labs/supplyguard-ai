import type { Approval, Execution } from '@/api/types';
import { Badge, Card, CardBody, CardHeader, CardTitle, type BadgeTone } from '@/components/ui';
import { formatDateTime } from './format';

const DECISION_TONE: Record<Approval['decision'], BadgeTone> = { APPROVE: 'success', REJECT: 'danger', REQUEST_CHANGES: 'warning' };
const EXEC_TONE: Record<Execution['status'], BadgeTone> = { PENDING: 'neutral', RUNNING: 'info', SUCCEEDED: 'success', FAILED: 'danger' };

export function ApprovalsExecutions({ approvals, executions }: { approvals: Approval[]; executions: Execution[] }) {
  return (
    <>
      <Card role="region" aria-labelledby="approvals-heading">
        <CardHeader>
          <CardTitle id="approvals-heading">Approvals</CardTitle>
        </CardHeader>
        <CardBody>
          {approvals.length === 0 ? (
            <p className="text-sm text-muted">No approvals yet</p>
          ) : (
            <ul className="space-y-3">
              {approvals.map((a) => (
                <li key={a.id} className="text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={DECISION_TONE[a.decision]}>{a.decision.replace('_', ' ')}</Badge>
                    <span className="font-mono text-xs text-muted">{a.recommendation_id}</span>
                  </div>
                  <p className="mt-1 text-muted">
                    {a.decided_by} · <time dateTime={a.decided_at}>{formatDateTime(a.decided_at)}</time>
                  </p>
                  {a.comments && <p className="mt-1 text-text">{a.comments}</p>}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
      <Card role="region" aria-labelledby="executions-heading">
        <CardHeader>
          <CardTitle id="executions-heading">Executions</CardTitle>
        </CardHeader>
        <CardBody>
          {executions.length === 0 ? (
            <p className="text-sm text-muted">No executions yet</p>
          ) : (
            <ul className="space-y-3">
              {executions.map((e) => (
                <li key={e.id} className="text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={EXEC_TONE[e.status]}>{e.status}</Badge>
                    <span className="font-mono text-xs text-muted">{e.id}</span>
                  </div>
                  <p className="mt-1 text-muted">
                    Started <time dateTime={e.started_at}>{formatDateTime(e.started_at)}</time>
                    {e.finished_at && (
                      <>
                        {' · '}finished <time dateTime={e.finished_at}>{formatDateTime(e.finished_at)}</time>
                      </>
                    )}
                  </p>
                  {e.error && <p className="mt-1 text-danger">{e.error}</p>}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </>
  );
}
