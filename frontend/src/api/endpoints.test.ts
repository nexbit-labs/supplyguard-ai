import { beforeEach, describe, expect, it } from 'vitest';
import { login } from './client';
import {
  getDashboardSummary,
  getRiskCase,
  getSupplier,
  listRiskCases,
  listSuppliers,
  simulateDisruption,
} from './endpoints';

describe('endpoints (mock backend)', () => {
  beforeEach(async () => {
    await login('admin', 'demo123');
  });

  it('lists suppliers with pagination', async () => {
    const p1 = await listSuppliers({ page: 1, page_size: 5 });
    expect(p1.count).toBe(12);
    expect(p1.results).toHaveLength(5);
    expect(p1.previous).toBeNull();
    expect(p1.next).toContain('page=2');
    const p3 = await listSuppliers({ page: 3, page_size: 5 });
    expect(p3.results).toHaveLength(2);
    expect(p3.next).toBeNull();
    expect(p3.previous).toContain('page=2');
  });

  it('filters suppliers by status, risk_level, search', async () => {
    const hold = await listSuppliers({ status: 'ON_HOLD' });
    expect(hold.results.map((s) => s.id)).toEqual(['SUP-1007']);
    const crit = await listSuppliers({ risk_level: 'CRITICAL' });
    expect(crit.results.map((s) => s.id)).toEqual(['SUP-1008']);
    const found = await listSuppliers({ search: 'pune' });
    expect(found.results[0].id).toBe('SUP-1008');
    const none = await listSuppliers({ search: 'zzz' });
    expect(none.count).toBe(0);
  });

  it('gets supplier detail and 404', async () => {
    const d = await getSupplier('SUP-1008');
    expect(d.kpis.on_time_delivery_pct).toBe(72);
    expect(d.disruption_history[0].risk_case_id).toBe('RC-000045');
    await expect(getSupplier('SUP-0')).rejects.toMatchObject({ code: 'NOT_FOUND' });
  });

  it('dashboard summary', async () => {
    const s = await getDashboardSummary();
    expect(s.exposure.currency).toBe('INR');
    expect(s.exposure_trend.length).toBeGreaterThan(0);
  });

  it('filters risk cases', async () => {
    const all = await listRiskCases();
    expect(all.count).toBe(6);
    const crit = await listRiskCases({ severity: 'CRITICAL' });
    expect(crit.results.map((c) => c.id)).toEqual(['RC-000043']);
    const pending = await listRiskCases({ status: 'PENDING_APPROVAL' });
    expect(pending.results[0].id).toBe('RC-000045');
  });

  it('gets full risk case detail', async () => {
    const d = await getRiskCase('RC-000045');
    expect(d.event.supplier_id).toBe('SUP-1008');
    expect(d.affected_pos[0].po_id).toBe('PO-1001');
    expect(d.analysis?.missing_data.length).toBeGreaterThan(0);
    expect(d.recommendations[0].id).toMatch(/^REC-/);
    await expect(getRiskCase('RC-999999')).rejects.toMatchObject({ code: 'NOT_FOUND', status: 404 });
  });

  it('simulate requires ADMIN', async () => {
    const r = await simulateDisruption('shipment_delay');
    expect(r.status).toBe('RECEIVED');
    await expect(simulateDisruption('no_such_scenario')).rejects.toMatchObject({ code: 'VALIDATION_ERROR', status: 400 });
    await login('manager', 'demo123');
    await expect(simulateDisruption('shipment_delay')).rejects.toMatchObject({ code: 'FORBIDDEN' });
  });
});
