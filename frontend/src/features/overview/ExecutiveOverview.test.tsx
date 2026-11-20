import { cloneElement, type ReactElement } from 'react';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { vi } from 'vitest';
import { renderWithProviders } from '@/test/renderWithProviders';
import { loginAs } from '@/test/loginAs';
import { server } from '@/mocks/server';
import { dashboardSummary } from '@/mocks/fixtures';
import { compactInr, shortDate } from '@/lib/format';

// jsdom has no layout: give charts a fixed size.
vi.mock('recharts', async (orig) => {
  const actual = await orig<typeof import('recharts')>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children as ReactElement<{ width: number; height: number }>, { width: 600, height: 256 }),
  };
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub);

const API = '*/api/v1';

describe('ExecutiveOverview', () => {
  beforeEach(async () => {
    await loginAs('admin');
  });

  it('renders heading, 6 KPIs with formatted values and the risk link', async () => {
    renderWithProviders({ path: '/' });
    expect(await screen.findByRole('heading', { level: 1, name: 'Executive Overview' })).toBeInTheDocument();
    const metrics = screen.getByRole('region', { name: 'Key metrics' });
    await within(metrics).findByText('₹44,75,000.00');
    for (const l of ['Suppliers', 'Open POs', 'Active shipments', 'Exposure', 'Open risk cases', 'Recovered value']) {
      expect(within(metrics).getByText(l)).toBeInTheDocument();
    }
    expect(within(metrics).getByText('₹21,00,000.00')).toBeInTheDocument();
    expect(within(metrics).getByText('23')).toBeInTheDocument();
    expect(within(metrics).getByRole('link', { name: String(dashboardSummary.open_cases) })).toHaveAttribute(
      'href',
      '/risk',
    );
    expect(screen.getByRole('img', { name: /exposure/i })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /risk cases/i })).toBeInTheDocument();
  });

  it('gives each chart a text alternative with latest value and range', async () => {
    renderWithProviders({ path: '/' });
    await screen.findByRole('img', { name: /exposure/i });
    expect(screen.getByText(/^Exposure: latest .* range .* to .*\.$/)).toBeInTheDocument();
    expect(screen.getByText(/^Open risk cases: latest .* range .* to .*\.$/)).toBeInTheDocument();
  });

  it('shows loading skeletons first', async () => {
    renderWithProviders({ path: '/' });
    expect(await screen.findAllByTestId('kpi-skeleton')).toHaveLength(6);
    expect(screen.getAllByTestId('chart-skeleton')).toHaveLength(2);
  });

  it('shows error with correlation id and retry recovers', async () => {
    server.use(
      http.get(`${API}/dashboard/summary`, () =>
        HttpResponse.json(
          { error: { code: 'DOWNSTREAM_ERROR', message: 'Boom upstream', details: {}, correlation_id: 'cid-123' } },
          { status: 500 },
        ),
      ),
    );
    renderWithProviders({ path: '/' });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Boom upstream');
    expect(alert).toHaveTextContent('cid-123');
    server.resetHandlers();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('₹44,75,000.00')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows empty state when both trends are empty', async () => {
    server.use(
      http.get(`${API}/dashboard/summary`, () =>
        HttpResponse.json({ ...dashboardSummary, exposure_trend: [], cases_trend: [] }),
      ),
    );
    renderWithProviders({ path: '/' });
    expect(await screen.findAllByText('No trend data yet')).toHaveLength(2);
    expect(screen.getByText('₹44,75,000.00')).toBeInTheDocument();
  });
});

describe('format helpers', () => {
  it('compactInr', () => {
    expect(compactInr(4_500_000)).toBe('₹45L');
    expect(compactInr(25_000_000)).toBe('₹2.5Cr');
    expect(compactInr(12_000)).toBe('₹12K');
  });
  it('shortDate', () => {
    expect(shortDate('2026-11-30')).toBe('30 Nov');
  });
});
