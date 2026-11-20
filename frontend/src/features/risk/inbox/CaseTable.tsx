import { Link } from 'react-router-dom';
import clsx from 'clsx';
import type { RiskCaseListItem } from '@/api/types';
import { Money, SeverityBadge, StatusBadge, Table, TBody, TD, TH, THead, TR } from '@/components/ui';
import { eventTypeLabel, relativeTime } from './format';

interface Props {
  items: RiskCaseListItem[];
  fresh: Set<string>;
  now: number;
}

export function CaseTable({ items, fresh, now }: Props) {
  return (
    <Table aria-label="Risk cases">
      <THead>
        <tr>
          <TH>Case</TH>
          <TH>Supplier</TH>
          <TH>Event</TH>
          <TH>Severity</TH>
          <TH>Status</TH>
          <TH className="text-right">Affected POs</TH>
          <TH className="text-right">Exposure</TH>
          <TH>Created</TH>
        </tr>
      </THead>
      <TBody>
        {items.map((c) => {
          const isNew = fresh.has(c.id);
          return (
            <TR key={c.id} className={clsx(isNew && 'bg-primary/10 ring-2 ring-inset ring-primary')}>
              <TD>
                <Link to={`/risk/${c.id}`} className="font-mono font-medium text-primary hover:underline">
                  {c.id}
                </Link>
                {isNew && (
                  <span className="ml-2 rounded bg-primary px-1.5 py-0.5 text-[10px] font-semibold uppercase text-primary-fg">
                    New
                  </span>
                )}
              </TD>
              <TD>{c.supplier_name}</TD>
              <TD>{eventTypeLabel(c.event_type)}</TD>
              <TD>
                <SeverityBadge severity={c.severity} />
              </TD>
              <TD>
                <StatusBadge status={c.status} />
              </TD>
              <TD className="text-right tabular-nums">{c.affected_po_count}</TD>
              <TD className="text-right">
                <Money value={c.exposure} />
              </TD>
              <TD>
                <time dateTime={c.created_at} title={new Date(c.created_at).toLocaleString()}>
                  {relativeTime(c.created_at, now)}
                </time>
              </TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
