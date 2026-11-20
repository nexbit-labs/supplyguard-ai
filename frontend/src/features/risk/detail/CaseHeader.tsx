import { Link } from 'react-router-dom';
import type { RiskCaseDetail } from '@/api/types';
import { SeverityBadge, StatusBadge } from '@/components/ui';
import { CopyButton } from './CopyButton';
import { formatDateTime } from './format';

export function CaseHeader({ detail }: { detail: RiskCaseDetail }) {
  const cid = detail.event.correlation_id;
  return (
    <header className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold text-text">{detail.id}</h1>
        <StatusBadge status={detail.status} />
        <SeverityBadge severity={detail.severity} />
      </div>
      <p className="text-sm text-text">{detail.title}</p>
      <p className="text-sm text-muted">
        Supplier:{' '}
        <Link className="font-medium text-primary hover:underline" to={`/suppliers/${detail.supplier_id}`}>
          {detail.supplier_name}
        </Link>
        {' · '}Created <time dateTime={detail.created_at}>{formatDateTime(detail.created_at)}</time>
        {' · '}Updated <time dateTime={detail.updated_at}>{formatDateTime(detail.updated_at)}</time>
      </p>
      <p className="flex flex-wrap items-center gap-1 text-xs text-muted">
        Correlation ID <span className="break-all font-mono">{cid}</span>
        <CopyButton value={cid} label="Copy correlation ID" />
      </p>
    </header>
  );
}
