import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/mocks/server';
import { riskCaseDetail45 } from '@/mocks/fixtures';
import { renderWithProviders } from '@/test/renderWithProviders';
import { loginAs } from '@/test/loginAs';

const URL = '*/api/v1/risk-cases/:id';

describe('Risk case detail /risk/:id', () => {
  beforeEach(async () => {
    await loginAs('admin');
  });

  it('renders header, event, PO rows and total exposure', async () => {
    renderWithProviders({ path: '/risk/RC-000045' });
    expect(await screen.findByRole('heading', { level: 1, name: 'RC-000045' })).toBeInTheDocument();
    const crumb = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(within(crumb).getByRole('link', { name: 'Risk Command Center' })).toHaveAttribute('href', '/risk');
    const header = screen.getByRole('heading', { level: 1 }).closest('header') as HTMLElement;
    expect(within(header).getByText('Pending approval')).toHaveAttribute('data-status', 'PENDING_APPROVAL');
    expect(header.querySelector('[data-severity="HIGH"]')).not.toBeNull();
    expect(screen.getByText('6f1c2a54-8d3e-4b7a-9c10-5e2f7a1b3d90')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy correlation ID' })).toBeInTheDocument();

    const event = screen.getByRole('region', { name: 'Disruption event' });
    expect(within(event).getByText('Factory outage')).toBeInTheDocument();
    expect(within(event).getByText('Line 2 fire, production halted')).toBeInTheDocument();
    expect(within(event).getByText('2 days')).toBeInTheDocument();
    expect(within(event).getByText('supplier-portal')).toBeInTheDocument();
    expect(within(event).getByText('EVT-000123')).toBeInTheDocument();

    const table = screen.getByRole('table', { name: /Affected purchase orders/ });
    for (const po of ['PO-1001', 'PO-1002', 'PO-1003']) expect(within(table).getByText(po)).toBeInTheDocument();
    expect(within(table).getByText('BLDC Motor 24V')).toBeInTheDocument();
    expect(screen.getByTestId('total-exposure')).toHaveTextContent('₹12,50,000.00');
  });

  it('links to the supplier page', async () => {
    renderWithProviders({ path: '/risk/RC-000045' });
    const link = await screen.findByRole('link', { name: riskCaseDetail45.supplier_name });
    expect(link).toHaveAttribute('href', '/suppliers/SUP-1008');
  });

  it('shows the timeline oldest to newest with the current entry highlighted', async () => {
    const reversed = { ...riskCaseDetail45, timeline: [...riskCaseDetail45.timeline].reverse() };
    server.use(http.get(URL, () => HttpResponse.json(reversed)));
    renderWithProviders({ path: '/risk/RC-000045' });
    const region = await screen.findByRole('region', { name: 'Status timeline' });
    const items = within(within(region).getByRole('list', { name: 'Timeline' })).getAllByRole('listitem');
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent('event received');
    expect(items[4]).toHaveTextContent('RECOMMENDATION_READY -> PENDING_APPROVAL');
    expect(items[4]).toHaveAttribute('data-current', 'true');
    expect(items[0]).not.toHaveAttribute('data-current');
    const step = within(region).getByText('Pending approval', { selector: 'li' });
    expect(step).toHaveAttribute('data-step-state', 'current');
    expect(screen.getByText('No approvals yet')).toBeInTheDocument();
    expect(screen.getByText('No executions yet')).toBeInTheDocument();
  });

  it('shows the analysis slot summary and recommendation link', async () => {
    renderWithProviders({ path: '/risk/RC-000045' });
    const slot = await screen.findByTestId('analysis-slot');
    expect(screen.getByRole('heading', { name: 'AI risk analysis' })).toBeInTheDocument();
    expect(within(slot).getByText(/fire at Pune Forge/)).toBeInTheDocument();
    for (const label of ['Facts', 'Estimates', 'Missing data']) {
      expect(within(slot).getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText('4 recovery options proposed')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Review in AI Recommendations/ })).toHaveAttribute(
      'href',
      '/recommendations?case=RC-000045',
    );
  });

  it('shows the in-progress panel when analysis is null', async () => {
    const pending = { ...riskCaseDetail45, analysis: null, status: 'ANALYZING' as const, affected_pos: [] };
    server.use(http.get(URL, () => HttpResponse.json(pending)));
    renderWithProviders({ path: '/risk/RC-000045' });
    const slot = await screen.findByTestId('analysis-slot');
    expect(slot).toHaveTextContent('AI analysis in progress');
    expect(within(slot).getByRole('status', { name: 'Analysis in progress' })).toBeInTheDocument();
    expect(screen.getByText('No affected purchase orders')).toBeInTheDocument();
  });

  it('shows not found for an unknown case', async () => {
    renderWithProviders({ path: '/risk/RC-999999' });
    expect(await screen.findByText('Risk case not found')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Back to Risk Command Center/ })).toHaveAttribute('href', '/risk');
  });

  it('shows an error with correlation id and retries on 500', async () => {
    let calls = 0;
    server.use(
      http.get(URL, () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json(
            { error: { code: 'DOWNSTREAM_ERROR', message: 'Backend exploded', correlation_id: 'cid-500' } },
            { status: 500 },
          );
        }
        return HttpResponse.json(riskCaseDetail45);
      }),
    );
    renderWithProviders({ path: '/risk/RC-000045' });
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend exploded');
    expect(screen.getByText(/cid-500/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'RC-000045' })).toBeInTheDocument();
  });
});
