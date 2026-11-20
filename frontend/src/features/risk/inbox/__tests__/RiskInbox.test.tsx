import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RouteObject } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import RiskCommandCenter from '@/pages/RiskCommandCenter';
import { server } from '@/mocks/server';
import { addSimulatedCase } from '@/mocks/fixtures';
import { loginAs } from '@/test/loginAs';
import { renderWithProviders } from '@/test/renderWithProviders';
import { SCENARIOS } from '@/mocks/scenarios';

/** Shows the signed-in role so tests can wait until /me has loaded. */
function RoleProbe() {
  const { me } = useAuth();
  return <span data-testid="role">{me?.role ?? ''}</span>;
}

const routes: RouteObject[] = [
  {
    path: '/risk',
    element: (
      <>
        <RoleProbe />
        <RiskCommandCenter />
      </>
    ),
  },
  { path: '/risk/:id', element: <div>case detail stub</div> },
];

const open = (path = '/risk') => renderWithProviders({ path, routes });
const rows = () => within(screen.getByRole('table', { name: 'Risk cases' })).getAllByRole('row').slice(1);

describe('Risk Command Center inbox', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  describe('as admin', () => {
    beforeEach(async () => {
      await loginAs('admin');
    });

    it('renders the case rows, newest first, with links', async () => {
      open();
      expect(screen.getByRole('heading', { level: 1, name: 'Risk Command Center' })).toBeInTheDocument();
      expect(await screen.findByRole('link', { name: 'RC-000045' })).toHaveAttribute('href', '/risk/RC-000045');
      const r = rows();
      expect(r).toHaveLength(6);
      expect(within(r[0]).getByText('Pune Forge & Motors')).toBeInTheDocument();
      expect(within(r[0]).getByText('Factory outage')).toBeInTheDocument();
      expect(screen.getByTestId('live-indicator')).toHaveTextContent(/Live · updated \d\d:\d\d:\d\d/);
    });

    it('severity filter writes the URL and narrows the list, and resets the page', async () => {
      const { router } = open('/risk?page=2');
      await screen.findByText(/No|of 6/);
      await userEvent.click(await screen.findByRole('button', { name: 'High' }));
      await waitFor(() => expect(router.state.location.search).toBe('?severity=HIGH'));
      await waitFor(() => expect(rows()).toHaveLength(2));
      expect(screen.getByRole('button', { name: 'High' })).toHaveAttribute('aria-pressed', 'true');
      expect(screen.queryByRole('link', { name: 'RC-000044' })).not.toBeInTheDocument();
    });

    it('status filter writes the URL', async () => {
      const { router } = open();
      await screen.findByRole('link', { name: 'RC-000045' });
      await userEvent.selectOptions(screen.getByLabelText('Status'), 'RECOVERED');
      await waitFor(() => expect(router.state.location.search).toBe('?status=RECOVERED'));
      await waitFor(() => expect(rows()).toHaveLength(1));
      expect(screen.getByRole('link', { name: 'RC-000043' })).toBeInTheDocument();
    });

    it('distinguishes filtered-empty from no cases', async () => {
      open('/risk?severity=LOW&status=ANALYZING');
      expect(await screen.findByText('No cases match these filters')).toBeInTheDocument();
      server.use(
        http.get('*/api/v1/risk-cases', () => HttpResponse.json({ count: 0, next: null, previous: null, results: [] })),
      );
      await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
      expect(await screen.findByText('No risk cases yet')).toBeInTheDocument();
    });

    it('shows an error state with retry', async () => {
      server.use(
        http.get('*/api/v1/risk-cases', () =>
          HttpResponse.json({ error: { code: 'DOWNSTREAM_ERROR', message: 'Backend is down' } }, { status: 500 }),
        ),
      );
      open();
      expect(await screen.findByRole('alert')).toHaveTextContent('Backend is down');
      server.resetHandlers();
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByRole('link', { name: 'RC-000045' })).toBeInTheDocument();
    });

    it('redirects ?case=RC-... to the case detail', async () => {
      const { router } = open('/risk?case=RC-000045');
      expect(await screen.findByText('case detail stub')).toBeInTheDocument();
      expect(router.state.location.pathname).toBe('/risk/RC-000045');
    });

    it('polls every 3s and picks up a case added server-side, with a New marker', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      open();
      await screen.findByRole('link', { name: 'RC-000045' });
      expect(screen.queryByText('New')).not.toBeInTheDocument();

      const { caseId } = addSimulatedCase(SCENARIOS[1]);
      expect(screen.queryByRole('link', { name: caseId })).not.toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100);
      });
      expect(await screen.findByRole('link', { name: caseId })).toBeInTheDocument();
      expect(screen.getByText('New')).toBeInTheDocument();
      expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent(`1 new case: ${caseId}`);

      // highlight fades after ~5s
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5100);
      });
      await waitFor(() => expect(screen.queryByText('New')).not.toBeInTheDocument());
    });

    it('simulate end-to-end: new case shows within 5s, then moves to Analyzing', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      open();
      await screen.findByRole('link', { name: 'RC-000045' });
      await user.click(await screen.findByRole('button', { name: 'Simulate Supplier Disruption' }));
      const dialog = screen.getByRole('dialog');
      await user.click(within(dialog).getByRole('radio', { name: /Quality hold/ }));
      await user.click(within(dialog).getByRole('button', { name: 'Run simulation' }));

      expect(await screen.findByRole('status')).toHaveTextContent(/Disruption event EVT-\d+ accepted/);
      // the mutation invalidates the list, so the case appears without waiting for a poll
      const link = await screen.findByRole('link', { name: 'RC-000046' });
      const row = link.closest('tr') as HTMLElement;
      expect(within(row).getByText('New')).toBeInTheDocument();
      expect(within(row).getByText('Received')).toBeInTheDocument();
      expect(rows()[0]).toBe(row);
      expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent('1 new case: RC-000046');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4500);
      });
      await waitFor(() => expect(within(screen.getByRole('link', { name: 'RC-000046' }).closest('tr') as HTMLElement).getByText('Analyzing')).toBeInTheDocument());
    });

    it.each([
      [403, 'FORBIDDEN', 'Admin role required.'],
      [400, 'VALIDATION_ERROR', 'scenario is not valid.'],
    ] as const)('shows the API message on a %s from simulate', async (status, code, message) => {
      server.use(
        http.post('*/api/v1/demo/simulate-disruption', () =>
          HttpResponse.json({ error: { code, message, details: {} } }, { status }),
        ),
      );
      open();
      await userEvent.click(await screen.findByRole('button', { name: 'Simulate Supplier Disruption' }));
      await userEvent.click(screen.getByRole('button', { name: 'Run simulation' }));
      expect(await screen.findByRole('alert')).toHaveTextContent(message);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  describe.each([
    ['viewer', 'VIEWER'],
    ['manager', 'PROCUREMENT_MANAGER'],
  ])('as %s', (username, role) => {
    it('hides the simulate button', async () => {
      await loginAs(username);
      open();
      await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent(role));
      await screen.findByRole('link', { name: 'RC-000045' });
      expect(screen.queryByRole('button', { name: /Simulate Supplier Disruption/ })).not.toBeInTheDocument();
    });
  });
});
