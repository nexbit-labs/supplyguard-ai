import type { EventType, RiskCaseState } from '@/api/types';

const EVENT_LABELS: Record<EventType, string> = {
  FACTORY_OUTAGE: 'Factory outage',
  SHIPMENT_DELAY: 'Shipment delay',
  QUALITY_HOLD: 'Quality hold',
  CAPACITY_REDUCTION: 'Capacity reduction',
  LOGISTICS_DISRUPTION: 'Logistics disruption',
};

export const eventTypeLabel = (t: EventType): string => EVENT_LABELS[t] ?? t;

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }) + ' UTC';
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-GB', { dateStyle: 'medium', timeZone: 'UTC' });
}

export function formatDuration(hours: number): string {
  if (hours >= 48 && hours % 24 === 0) return `${hours / 24} days`;
  return `${hours} hours`;
}

/** CONTRACTS section 7 happy path. */
export const STATE_PATH: RiskCaseState[] = [
  'RECEIVED',
  'ANALYZING',
  'RECOMMENDATION_READY',
  'PENDING_APPROVAL',
  'APPROVED',
  'EXECUTING',
  'RECOVERED',
  'CLOSED',
];

export const STATE_LABELS: Record<RiskCaseState, string> = {
  RECEIVED: 'Received',
  ANALYZING: 'Analyzing',
  RECOMMENDATION_READY: 'Recommendation ready',
  PENDING_APPROVAL: 'Pending approval',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  EXECUTING: 'Executing',
  EXECUTION_FAILED: 'Execution failed',
  RECOVERED: 'Recovered',
  CLOSED: 'Closed',
};

/** Steps shown in the stepper: happy path, with the danger branch swapped in at its position. */
export function stepperSteps(status: RiskCaseState): RiskCaseState[] {
  if (status === 'REJECTED') return [...STATE_PATH.slice(0, 4), 'REJECTED'];
  if (status === 'EXECUTION_FAILED') return [...STATE_PATH.slice(0, 6), 'EXECUTION_FAILED'];
  return STATE_PATH;
}
