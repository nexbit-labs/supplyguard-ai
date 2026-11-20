import type { ReactNode } from 'react';
import type { DisruptionEvent } from '@/api/types';
import { Card, CardBody, CardHeader, CardTitle, SeverityBadge } from '@/components/ui';
import { eventTypeLabel, formatDateTime, formatDuration } from './format';

export function EventCard({ event }: { event: DisruptionEvent }) {
  const rows: [string, ReactNode][] = [
    ['Event type', eventTypeLabel(event.event_type)],
    ['Severity', <SeverityBadge key="s" severity={event.severity} />],
    ['Event time', <time key="t" dateTime={event.event_time}>{formatDateTime(event.event_time)}</time>],
    ['Expected duration', formatDuration(event.expected_duration_hours)],
    ['Source system', event.source_system],
    ['Event ID', <span key="i" className="font-mono text-xs">{event.event_id}</span>],
  ];
  return (
    <Card role="region" aria-labelledby="event-heading">
      <CardHeader>
        <CardTitle id="event-heading">Disruption event</CardTitle>
      </CardHeader>
      <CardBody>
        <p className="mb-4 text-sm text-text">{event.description}</p>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          {rows.map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs uppercase tracking-wide text-muted">{k}</dt>
              <dd className="mt-0.5 text-text">{v}</dd>
            </div>
          ))}
        </dl>
      </CardBody>
    </Card>
  );
}
