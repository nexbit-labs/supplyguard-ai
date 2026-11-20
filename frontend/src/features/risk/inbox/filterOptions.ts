import type { RiskCaseState, Severity } from '@/api/types';
import { STATUS_META } from '@/components/ui/StatusBadge';

export const SEVERITIES: Severity[] = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];
export const STATES = Object.keys(STATUS_META) as RiskCaseState[];
