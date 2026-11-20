import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/mocks/server';
import { renderWithProviders } from '@/test/renderWithProviders';
import { loginAs } from '@/test/loginAs';
import { supplierDetail } from '@/mocks/fixtures';
import { eventTypeLabel } from './format';

describe('SupplierDetail page', () => {
  beforeEach(async () => {
    await loginAs('admin');
  });

  it('formats event types in words', () => {
    expect(eventTypeLabel('FACTORY_OUTAGE')).toBe('Factory outage');
  });

  it('shows header and KPIs', async () => {
    const detail = supplierDetail('SUP-1008')!;
    renderWithProviders({ path: '/suppliers/SUP-1008' });

    expect(await screen.findByRole('heading', { level: 1, name: detail.name })).toBeInTheDocument();
    expect(screen.getByText('SUP-1008')).toBeInTheDocument();
    expect(screen.getByText(`${detail.country} · ${detail.category}`)).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    const meter = screen.getByRole('meter', { name: 'Risk score' });
    expect(meter).toHaveAttribute('aria-valuenow', String(detail.risk_score));
    expect(screen.getByRole('link', { name: detail.contact_email })).toHaveAttribute(
      'href',
      `mailto:${detail.contact_email}`,
    );

    for (const label of [
      'On-time delivery',
      'Quality pass',
      'Avg lead time',
      'Open PO value',
      'Disruptions (12 months)',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText(`${detail.kpis.on_time_delivery_pct}%`)).toBeInTheDocument();
    expect(screen.getByText(`${detail.kpis.avg_lead_time_days} days`)).toBeInTheDocument();
  });

  it('lists disruption history with a case link', async () => {
    const detail = supplierDetail('SUP-1008')!;
    expect(detail.disruption_history.length).toBeGreaterThan(0);
    const first = detail.disruption_history[0];
    renderWithProviders({ path: '/suppliers/SUP-1008' });

    const list = await screen.findByRole('list', { name: 'Disruption history' });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(detail.disruption_history.length);
    expect(within(items[0]).getByText('Factory outage')).toBeInTheDocument();
    expect(within(items[0]).getByText(first.description)).toBeInTheDocument();
    expect(within(items[0]).getByText('High')).toBeInTheDocument();
    const link = within(items[0]).getByRole('link', { name: `View case ${first.risk_case_id}` });
    expect(link).toHaveAttribute('href', `/risk?case=${first.risk_case_id}`);
  });

  it('shows "No case" and sorts newest first', async () => {
    const detail = supplierDetail('SUP-1008')!;
    server.use(
      http.get('*/api/v1/suppliers/:id', () =>
        HttpResponse.json({
          ...detail,
          disruption_history: [
            { event_id: 'E1', event_type: 'QUALITY_HOLD', severity: 'LOW', event_time: '2026-01-01T00:00:00Z', description: 'older', risk_case_id: null },
            { event_id: 'E2', event_type: 'SHIPMENT_DELAY', severity: 'MEDIUM', event_time: '2026-06-01T00:00:00Z', description: 'newer', risk_case_id: null },
          ],
        }),
      ),
    );
    renderWithProviders({ path: '/suppliers/SUP-1008' });
    const items = within(await screen.findByRole('list', { name: 'Disruption history' })).getAllByRole('listitem');
    expect(within(items[0]).getByText('newer')).toBeInTheDocument();
    expect(within(items[1]).getByText('older')).toBeInTheDocument();
    expect(within(items[0]).getByText('No case')).toBeInTheDocument();
  });

  it('shows empty state when no history', async () => {
    const detail = supplierDetail('SUP-1001')!;
    server.use(
      http.get('*/api/v1/suppliers/:id', () => HttpResponse.json({ ...detail, disruption_history: [] })),
    );
    renderWithProviders({ path: '/suppliers/SUP-1001' });
    expect(await screen.findByText('No disruptions recorded')).toBeInTheDocument();
  });

  it('shows Supplier not found for unknown id', async () => {
    renderWithProviders({ path: '/suppliers/SUP-9999' });
    expect(await screen.findByRole('heading', { name: 'Supplier not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Back to suppliers/ })).toHaveAttribute('href', '/suppliers');
  });

  it('shows error with retry and correlation id on 500', async () => {
    server.use(
      http.get('*/api/v1/suppliers/:id', () =>
        HttpResponse.json(
          { error: { code: 'DOWNSTREAM_ERROR', message: 'Boom', details: {}, correlation_id: 'cid-123' } },
          { status: 500 },
        ),
      ),
    );
    renderWithProviders({ path: '/suppliers/SUP-1008' });
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Boom')).toBeInTheDocument();
    expect(within(alert).getByText(/cid-123/)).toBeInTheDocument();
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('breadcrumb links back to the supplier list', async () => {
    const { router } = renderWithProviders({ path: '/suppliers/SUP-1008' });
    await screen.findByRole('heading', { level: 1, name: supplierDetail('SUP-1008')!.name });
    const nav = screen.getByRole('navigation', { name: 'Breadcrumb' });
    const link = within(nav).getByRole('link', { name: 'Suppliers' });
    expect(link).toHaveAttribute('href', '/suppliers');
    await userEvent.click(link);
    expect(router.state.location.pathname).toBe('/suppliers');
  });
});
