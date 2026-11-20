import { Link } from 'react-router-dom';
import { Card, CardHeader, CardTitle, EmptyState, SeverityBadge } from '@/components/ui';
import type { SupplierDisruption } from '@/api/types';
import { formatDateTime } from '@/lib/format';
import { eventTypeLabel } from './format';

export function DisruptionHistory({ items }: { items: SupplierDisruption[] }) {
  const sorted = [...items].sort((a, b) => new Date(b.event_time).getTime() - new Date(a.event_time).getTime());
  return (
    <Card>
      <CardHeader>
        <CardTitle>Disruption history</CardTitle>
      </CardHeader>
      {sorted.length === 0 ? (
        <EmptyState title="No disruptions recorded" description="No disruption events have been logged for this supplier." />
      ) : (
        <ul aria-label="Disruption history" className="divide-y divide-border">
          {sorted.map((d) => (
            <li key={d.event_id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={d.severity} />
                  <span className="text-sm font-medium text-text">{eventTypeLabel(d.event_type)}</span>
                  <time dateTime={d.event_time} className="text-xs text-muted">
                    {formatDateTime(d.event_time)}
                  </time>
                </div>
                <p className="text-sm text-text">{d.description}</p>
              </div>
              <div className="shrink-0 text-sm">
                {d.risk_case_id ? (
                  <Link className="font-medium text-info hover:underline" to={`/risk?case=${encodeURIComponent(d.risk_case_id)}`}>
                    View case {d.risk_case_id}
                  </Link>
                ) : (
                  <span className="text-muted">No case</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
