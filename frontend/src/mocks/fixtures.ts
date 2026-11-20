import type {
  DashboardSummary,
  DisruptionEvent,
  EventType,
  Me,
  Money,
  RiskCaseDetail,
  RiskCaseListItem,
  RiskCaseState,
  Role,
  Severity,
  Supplier,
  SupplierDetail,
} from '@/api/types';
import type { SimulateScenarioOption } from './scenarios';

export const TENANT = { id: 'TEN-ACME', name: 'Acme Manufacturing' };
export const DEMO_PASSWORD = 'demo123';

const inr = (amount: string): Money => ({ amount, currency: 'INR' });

export interface MockUser {
  id: string;
  username: string;
  full_name: string;
  email: string;
  role: Role;
  password: string;
}

export const users: MockUser[] = [
  { id: 'USR-1', username: 'admin', full_name: 'Asha Admin', email: 'admin@acme.example', role: 'ADMIN', password: DEMO_PASSWORD },
  { id: 'USR-2', username: 'manager', full_name: 'Pranav Procurement', email: 'manager@acme.example', role: 'PROCUREMENT_MANAGER', password: DEMO_PASSWORD },
  { id: 'USR-3', username: 'viewer', full_name: 'Vikram Viewer', email: 'viewer@acme.example', role: 'VIEWER', password: DEMO_PASSWORD },
];

export function meFor(user: MockUser): Me {
  return {
    user: { id: user.id, username: user.username, full_name: user.full_name, email: user.email },
    tenant: TENANT,
    role: user.role,
  };
}

const sup = (
  n: number,
  name: string,
  country: string,
  category: string,
  status: Supplier['status'],
  risk_level: Supplier['risk_level'],
  risk_score: number,
  open_po_count: number,
  otd: number,
): Supplier => ({
  id: `SUP-${1000 + n}`,
  name,
  country,
  category,
  status,
  risk_level,
  risk_score,
  open_po_count,
  on_time_delivery_pct: otd,
});

export const suppliers: Supplier[] = [
  sup(1, 'Bharat Steelworks', 'India', 'Raw Materials', 'ACTIVE', 'LOW', 18, 4, 96),
  sup(2, 'Shenzhen Microtech', 'China', 'Electronics', 'ACTIVE', 'MEDIUM', 44, 6, 89),
  sup(3, 'Rhine Precision GmbH', 'Germany', 'Machined Parts', 'ACTIVE', 'LOW', 22, 3, 97),
  sup(4, 'Gujarat Polymers', 'India', 'Plastics', 'ACTIVE', 'LOW', 25, 5, 94),
  sup(5, 'Taipei Semiconductor', 'Taiwan', 'Electronics', 'ACTIVE', 'HIGH', 71, 7, 81),
  sup(6, 'Chennai Castings', 'India', 'Castings', 'ACTIVE', 'MEDIUM', 38, 4, 90),
  sup(7, 'Hanoi Textiles', 'Vietnam', 'Packaging', 'ON_HOLD', 'MEDIUM', 52, 2, 85),
  sup(8, 'Pune Forge & Motors', 'India', 'Motors', 'ACTIVE', 'CRITICAL', 92, 9, 72),
  sup(9, 'Lagos Logistics Co', 'Nigeria', 'Logistics', 'ACTIVE', 'HIGH', 66, 3, 78),
  sup(10, 'Osaka Bearings', 'Japan', 'Bearings', 'ACTIVE', 'LOW', 15, 5, 98),
  sup(11, 'Monterrey Wire & Cable', 'Mexico', 'Cabling', 'ACTIVE', 'MEDIUM', 41, 4, 91),
  sup(12, 'Dhaka Fasteners', 'Bangladesh', 'Fasteners', 'INACTIVE', 'LOW', 20, 0, 93),
];

export function supplierDetail(id: string): SupplierDetail | undefined {
  const s = suppliers.find((x) => x.id === id);
  if (!s) return undefined;
  const history = riskCaseList
    .filter((c) => c.supplier_id === id)
    .map((c) => ({
      event_id: `EVT-${c.id.slice(3)}`,
      event_type: c.event_type,
      severity: c.severity,
      event_time: c.created_at,
      description: c.title,
      risk_case_id: c.id,
    }));
  return {
    ...s,
    contact_email: `ops@${s.name.toLowerCase().replace(/[^a-z]+/g, '')}.example`,
    kpis: {
      on_time_delivery_pct: s.on_time_delivery_pct,
      quality_pass_pct: Math.min(99.5, s.on_time_delivery_pct + 2),
      avg_lead_time_days: 6 + (s.risk_score % 10),
      open_po_value: inr(String(s.open_po_count * 1_250_000) + '.00'),
      disruptions_last_12m: history.length + (s.risk_score > 60 ? 2 : 0),
    },
    disruption_history: history,
  };
}

const rc = (
  n: number,
  status: RiskCaseState,
  severity: Severity,
  supplierN: number,
  event_type: EventType,
  title: string,
  risk_score: number,
  pos: number,
  exposure: string,
  created: string,
): RiskCaseListItem => ({
  id: `RC-${String(n).padStart(6, '0')}`,
  status,
  severity,
  supplier_id: `SUP-${1000 + supplierN}`,
  supplier_name: suppliers[supplierN - 1].name,
  event_type,
  title,
  risk_score,
  affected_po_count: pos,
  exposure: inr(exposure),
  created_at: created,
  updated_at: created,
});

export const riskCaseList: RiskCaseListItem[] = [
  rc(45, 'PENDING_APPROVAL', 'HIGH', 8, 'FACTORY_OUTAGE', 'Line 2 fire halts production at Pune Forge & Motors', 92, 3, '1250000.00', '2026-12-02T10:15:00Z'),
  rc(44, 'ANALYZING', 'MEDIUM', 5, 'SHIPMENT_DELAY', 'Port congestion delays Taipei Semiconductor shipment', 58, 2, '640000.00', '2026-12-01T16:40:00Z'),
  rc(43, 'RECOVERED', 'CRITICAL', 9, 'LOGISTICS_DISRUPTION', 'Road blockade stops Lagos Logistics trucks', 88, 4, '2100000.00', '2026-11-28T08:05:00Z'),
  rc(42, 'EXECUTION_FAILED', 'HIGH', 2, 'QUALITY_HOLD', 'Batch QH-77 on hold at Shenzhen Microtech', 74, 2, '480000.00', '2026-11-26T12:30:00Z'),
  rc(41, 'REJECTED', 'LOW', 6, 'CAPACITY_REDUCTION', 'Chennai Castings reduces capacity 10%', 31, 1, '95000.00', '2026-11-24T09:00:00Z'),
  rc(40, 'CLOSED', 'MEDIUM', 11, 'SHIPMENT_DELAY', 'Cable shipment from Monterrey delayed 3 days', 47, 2, '310000.00', '2026-11-20T14:20:00Z'),
];

/** 14 consecutive days ending 2026-11-30 (fixed anchor, Date arithmetic). */
function trendDate(i: number): string {
  const d = new Date(Date.UTC(2026, 10, 30));
  d.setUTCDate(d.getUTCDate() - (13 - i));
  return d.toISOString().slice(0, 10);
}

export const dashboardSummary: DashboardSummary = {
  suppliers: suppliers.length,
  open_pos: suppliers.reduce((a, s) => a + s.open_po_count, 0),
  active_shipments: 23,
  exposure: inr('4475000.00'),
  open_cases: riskCaseList.filter((c) => !['RECOVERED', 'CLOSED', 'REJECTED'].includes(c.status)).length,
  recovered_value: inr('2100000.00'),
  exposure_trend: Array.from({ length: 14 }, (_, i) => ({
    date: trendDate(i),
    value: 2_000_000 + ((i * 337_000) % 2_800_000),
  })),
  cases_trend: Array.from({ length: 14 }, (_, i) => ({
    date: trendDate(i),
    value: 1 + ((i * 3) % 6),
  })),
};

const event45: DisruptionEvent = {
  event_id: 'EVT-000123',
  tenant_id: TENANT.id,
  supplier_id: 'SUP-1008',
  event_type: 'FACTORY_OUTAGE',
  severity: 'HIGH',
  event_time: '2026-12-02T10:15:00Z',
  expected_duration_hours: 48,
  description: 'Line 2 fire, production halted',
  source_system: 'supplier-portal',
  correlation_id: '6f1c2a54-8d3e-4b7a-9c10-5e2f7a1b3d90',
};

export const riskCaseDetail45: RiskCaseDetail = {
  ...riskCaseList[0],
  event: event45,
  affected_pos: [
    { po_id: 'PO-1001', item_id: 'ITM-501', item_name: 'BLDC Motor 24V', qty: 400, value: inr('560000.00'), due_date: '2026-12-03', days_late_estimate: 4, stock_cover_days: 2 },
    { po_id: 'PO-1002', item_id: 'ITM-502', item_name: 'Motor Housing', qty: 800, value: inr('420000.00'), due_date: '2026-12-06', days_late_estimate: 2, stock_cover_days: 5 },
    { po_id: 'PO-1003', item_id: 'ITM-503', item_name: 'Rotor Assembly', qty: 250, value: inr('270000.00'), due_date: '2026-12-10', days_late_estimate: 0, stock_cover_days: 9 },
  ],
  analysis: {
    risk_case_id: 'RC-000045',
    risk_level: 'HIGH',
    risk_score: 92,
    summary:
      'A fire at Pune Forge & Motors Line 2 halts production for about 48 hours. Three open POs worth INR 1,250,000 are exposed; PO-1001 is most urgent with only 2 days of stock cover.',
    affected_entities: [
      { type: 'SUPPLIER', id: 'SUP-1008' },
      { type: 'PO', id: 'PO-1001' },
      { type: 'PO', id: 'PO-1002' },
      { type: 'PO', id: 'PO-1003' },
    ],
    evidence: [
      { source: 'ERP', entity: 'PO-1001', field: 'due_date', item_id: 'ITM-501', value: '2026-12-03' },
      { source: 'ERP', entity: 'ITM-501', field: 'on_hand', location_id: 'WH-PUNE-1', value: 160 },
      { source: 'EVENT', entity: 'EVT-000123', field: 'expected_duration_hours', value: 48 },
    ],
    facts: [
      'Supplier SUP-1008 reported a Line 2 fire at 2026-12-02T10:15:00Z.',
      'PO-1001 (400 units BLDC Motor 24V) is due 2026-12-03.',
      'On-hand stock of ITM-501 is 160 units, about 2 days of cover.',
    ],
    estimates: [
      { statement: 'PO-1001 will arrive about 4 days late.', basis: '48h outage plus 2 days restart and transit time' },
    ],
    assumptions: ['Outage duration equals the reported 48 hours.', 'Other supplier lines are unaffected.'],
    missing_data: ['Supplier capacity after restart not provided', 'Customer penalty terms for late delivery'],
    confidence: 'MEDIUM',
  },
  recommendations: [
    {
      id: 'REC-000101',
      rank: 1,
      option_type: 'ALTERNATE_SUPPLIER',
      proposed_action: { po_id: 'PO-1001', change: { supplier_id: 'SUP-1003', qty: 400 } },
      action_steps: ['Raise replacement PO with Rhine Precision GmbH', 'Cancel PO-1001 at SUP-1008', 'Notify production planning'],
      estimated_lead_time_days: 2,
      estimated_cost_delta: inr('33600.00'),
      trade_offs: ['Unit price 6% higher', 'New supplier onboarding checks already complete'],
      evidence: [{ source: 'ERP', entity: 'SUP-1003', field: 'unit_price', value: '84.00' }],
      assumptions: ['SUP-1003 has capacity for 400 units'],
      data_gaps: [],
    },
    {
      id: 'REC-000102',
      rank: 2,
      option_type: 'INVENTORY_TRANSFER',
      proposed_action: { po_id: 'PO-1001', change: { item_id: 'ITM-501', from_location_id: 'WH-PUNE-2', to_location_id: 'WH-PUNE-1', qty: 120 } },
      action_steps: ['Transfer 120 units from WH-PUNE-2', 'Re-plan remaining 280 units'],
      estimated_lead_time_days: 1,
      estimated_cost_delta: inr('9000.00'),
      trade_offs: ['Covers only 30% of the order'],
      evidence: [{ source: 'ERP', entity: 'ITM-501', field: 'on_hand', location_id: 'WH-PUNE-2', value: 120 }],
      assumptions: [],
      data_gaps: ['Safety stock policy for WH-PUNE-2 unknown'],
    },
    {
      id: 'REC-000103',
      rank: 3,
      option_type: 'REVISED_SCHEDULE',
      proposed_action: { po_id: 'PO-1001', change: { expected_date: '2026-12-07' } },
      action_steps: ['Agree revised delivery date with supplier'],
      estimated_lead_time_days: 4,
      estimated_cost_delta: inr('0.00'),
      trade_offs: ['Production line idle for roughly 2 days'],
      evidence: [{ source: 'ERP', entity: 'PO-1001', field: 'due_date', item_id: 'ITM-501', value: '2026-12-03' }],
      assumptions: ['Supplier honours the revised date'],
      data_gaps: [],
    },
    {
      id: 'REC-000104',
      rank: 4,
      option_type: 'SPLIT_ORDER',
      proposed_action: { po_id: 'PO-1002', change: { supplier_id: 'SUP-1003', qty: 300 } },
      action_steps: ['Move 300 units of PO-1002 to Rhine Precision GmbH'],
      estimated_lead_time_days: null,
      estimated_cost_delta: null,
      trade_offs: ['Splits the order across two suppliers'],
      evidence: [{ source: 'ERP', entity: 'PO-1002', field: 'qty', item_id: 'ITM-502', value: 800 }],
      assumptions: [],
      data_gaps: ['Alternate supplier lead time and unit price for ITM-502 not provided'],
    },
  ],
  no_viable_option_reason: null,
  approvals: [],
  executions: [],
  timeline: [
    { at: '2026-12-02T10:15:00Z', actor: 'supplier-portal', action: 'EVENT_RECEIVED', detail: 'EVT-000123 received' },
    { at: '2026-12-02T10:15:20Z', actor: 'system', action: 'STATE_CHANGED', detail: 'RECEIVED -> ANALYZING' },
    { at: '2026-12-02T10:16:05Z', actor: 'risk-analysis-agent', action: 'ANALYSIS_STORED', detail: 'Risk score 92' },
    { at: '2026-12-02T10:16:40Z', actor: 'recovery-planning-agent', action: 'RECOMMENDATIONS_STORED', detail: '3 options' },
    { at: '2026-12-02T10:16:41Z', actor: 'system', action: 'STATE_CHANGED', detail: 'RECOMMENDATION_READY -> PENDING_APPROVAL' },
  ],
};

/** Detail for any case id: RC-000045 is the full one, others are derived stubs. */
export function riskCaseDetail(id: string): RiskCaseDetail | undefined {
  const simulated = simulatedDetails.get(id);
  if (simulated) return simulated;
  if (id === 'RC-000045') return riskCaseDetail45;
  const item = riskCaseList.find((c) => c.id === id);
  if (!item) return undefined;
  return {
    ...item,
    event: {
      ...event45,
      event_id: `EVT-${id.slice(3)}`,
      supplier_id: item.supplier_id,
      event_type: item.event_type,
      severity: item.severity,
      event_time: item.created_at,
      description: item.title,
    },
    affected_pos: [],
    no_viable_option_reason: null,
    analysis: null,
    recommendations: [],
    approvals: [],
    executions: [],
    timeline: [{ at: item.created_at, actor: 'system', action: 'CASE_CREATED', detail: item.title }],
  };
}

// ---- Stateful simulate-disruption (R-04) ----
const initialCases = [...riskCaseList];
const simulatedDetails = new Map<string, RiskCaseDetail>();
const simTimers = new Set<ReturnType<typeof setTimeout>>();
let nextCaseNumber = 46;

export const ANALYZING_DELAY_MS = 1500;

/** Adds a RECEIVED case (newest first) for the scenario and moves it to ANALYZING shortly after. */
export function addSimulatedCase(scenario: SimulateScenarioOption): { caseId: string; eventId: string } {
  const n = nextCaseNumber++;
  const id = `RC-${String(n).padStart(6, '0')}`;
  const eventId = `EVT-${String(900000 + n)}`;
  const now = new Date().toISOString();
  const supplier = suppliers.find((s) => s.id === scenario.supplier_id) ?? suppliers[0];
  const item: RiskCaseListItem = {
    id,
    status: 'RECEIVED',
    severity: scenario.severity,
    supplier_id: supplier.id,
    supplier_name: supplier.name,
    event_type: scenario.event_type,
    title: `${scenario.label}`,
    risk_score: 0,
    affected_po_count: scenario.affected_po_count,
    exposure: inr(scenario.exposure),
    created_at: now,
    updated_at: now,
  };
  riskCaseList.unshift(item);
  const detail: RiskCaseDetail = {
    ...item,
    event: {
      ...event45,
      event_id: eventId,
      supplier_id: supplier.id,
      event_type: scenario.event_type,
      severity: scenario.severity,
      event_time: now,
      description: item.title,
    },
    affected_pos: [],
    analysis: null,
    recommendations: [],
    no_viable_option_reason: null,
    approvals: [],
    executions: [],
    timeline: [{ at: now, actor: 'system', action: 'RECEIVED', detail: `Event ${eventId} received` }],
  };
  simulatedDetails.set(id, detail);
  const timer = setTimeout(() => {
    simTimers.delete(timer);
    const at = new Date().toISOString();
    item.status = 'ANALYZING';
    item.updated_at = at;
    const current = simulatedDetails.get(id);
    if (current) {
      simulatedDetails.set(id, {
        ...current,
        status: 'ANALYZING',
        updated_at: at,
        timeline: [...current.timeline, { at, actor: 'system', action: 'ANALYZING', detail: 'Analysis started' }],
      });
    }
  }, ANALYZING_DELAY_MS);
  simTimers.add(timer);
  return { caseId: id, eventId };
}

/** Restores the fixture list and drops simulated cases/timers (called after every test). */
export function resetMockState(): void {
  simTimers.forEach(clearTimeout);
  simTimers.clear();
  simulatedDetails.clear();
  riskCaseList.splice(0, riskCaseList.length, ...initialCases.map((c) => ({ ...c })));
  nextCaseNumber = 46;
}
