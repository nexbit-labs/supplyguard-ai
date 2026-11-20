import type { EventType, Severity } from '@/api/types';

export interface SimulateScenarioOption {
  /** Sent as `scenario` to POST /demo/simulate-disruption. */
  name: string;
  label: string;
  description: string;
  /** Used by the MSW mock to build the new case; the real backend decides itself. */
  event_type: EventType;
  severity: Severity;
  supplier_id: string;
  affected_po_count: number;
  exposure: string;
}

export const SCENARIOS: SimulateScenarioOption[] = [
  {
    name: 'factory_outage_SUP-1008',
    label: 'Factory outage at Pune Forge & Motors',
    description: 'Line fire halts production for about 48 hours.',
    event_type: 'FACTORY_OUTAGE',
    severity: 'HIGH',
    supplier_id: 'SUP-1008',
    affected_po_count: 3,
    exposure: '1250000.00',
  },
  {
    name: 'shipment_delay',
    label: 'Shipment delay',
    description: 'Port congestion delays an inbound shipment by several days.',
    event_type: 'SHIPMENT_DELAY',
    severity: 'MEDIUM',
    supplier_id: 'SUP-1005',
    affected_po_count: 2,
    exposure: '640000.00',
  },
  {
    name: 'quality_hold',
    label: 'Quality hold',
    description: 'A production batch is quarantined after a failed inspection.',
    event_type: 'QUALITY_HOLD',
    severity: 'HIGH',
    supplier_id: 'SUP-1002',
    affected_po_count: 2,
    exposure: '480000.00',
  },
  {
    name: 'logistics_blockade',
    label: 'Logistics blockade',
    description: 'Road blockade stops trucks serving a supplier.',
    event_type: 'LOGISTICS_DISRUPTION',
    severity: 'CRITICAL',
    supplier_id: 'SUP-1009',
    affected_po_count: 4,
    exposure: '2100000.00',
  },
];
