import type { SupplierStatus } from '@/api/types';
import type { BadgeTone } from '@/components/ui';

/** Single source for supplier status label + badge tone. */
export const SUPPLIER_STATUS: Record<SupplierStatus, { label: string; tone: BadgeTone }> = {
  ACTIVE: { label: 'Active', tone: 'success' },
  ON_HOLD: { label: 'On hold', tone: 'warning' },
  INACTIVE: { label: 'Inactive', tone: 'neutral' },
};

export const STATUS_OPTIONS: { value: SupplierStatus; label: string }[] = (
  Object.keys(SUPPLIER_STATUS) as SupplierStatus[]
).map((value) => ({ value, label: SUPPLIER_STATUS[value].label }));
