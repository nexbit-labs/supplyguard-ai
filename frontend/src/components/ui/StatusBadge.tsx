import { Badge, type BadgeProps, type BadgeTone } from './Badge';

export type RiskCaseStatus =
  | 'RECEIVED'
  | 'ANALYZING'
  | 'RECOMMENDATION_READY'
  | 'PENDING_APPROVAL'
  | 'APPROVED'
  | 'EXECUTING'
  | 'RECOVERED'
  | 'CLOSED'
  | 'REJECTED'
  | 'EXECUTION_FAILED';

// eslint-disable-next-line react-refresh/only-export-components -- metadata table shared with the badge; pure constant
export const STATUS_META: Record<RiskCaseStatus, { label: string; tone: BadgeTone }> = {
  RECEIVED: { label: 'Received', tone: 'neutral' },
  ANALYZING: { label: 'Analyzing', tone: 'info' },
  RECOMMENDATION_READY: { label: 'Recommendation ready', tone: 'info' },
  PENDING_APPROVAL: { label: 'Pending approval', tone: 'warning' },
  APPROVED: { label: 'Approved', tone: 'success' },
  EXECUTING: { label: 'Executing', tone: 'info' },
  RECOVERED: { label: 'Recovered', tone: 'success' },
  CLOSED: { label: 'Closed', tone: 'neutral' },
  REJECTED: { label: 'Rejected', tone: 'danger' },
  EXECUTION_FAILED: { label: 'Execution failed', tone: 'danger' },
};

export interface StatusBadgeProps extends Omit<BadgeProps, 'tone'> {
  status: RiskCaseStatus;
}

export function StatusBadge({ status, ...rest }: StatusBadgeProps) {
  const meta = STATUS_META[status];
  return (
    <Badge tone={meta.tone} data-status={status} {...rest}>
      {meta.label}
    </Badge>
  );
}
