import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders } from '@/test/renderWithProviders';
import { loginAs } from '@/test/loginAs';
import { server } from '@/mocks/server';
import { suppliers } from '@/mocks/fixtures';

const rowsOf = () => within(screen.getByRole('table', { name: 'Suppliers' })).getAllByRole('row').slice(1);
const query = (r: { router: { state: { location: { search: string } } } }) =>
  new URLSearchParams(r.router.state.location.search);
const PUNE = 'Pune Forge & Motors';

describe('SupplierIntelligence list', () => {
  beforeEach(async () => {
    await loginAs('admin');
  });

  it('renders fixture rows sorted by risk with detail links', async () => {
    renderWithProviders({ path: '/suppliers' });
    expect(await screen.findByRole('heading', { level: 1, name: 'Supplier Intelligence' })).toBeInTheDocument();
    await screen.findByRole('link', { name: PUNE });
    expect(screen.getByText('12 suppliers')).toBeInTheDocument();
    const rows = rowsOf();
    expect(rows).toHaveLength(10);
    expect(within(rows[0]).getByRole('link')).toHaveTextContent(PUNE);
    expect(screen.getByRole('link', { name: PUNE })).toHaveAttribute('href', '/suppliers/SUP-1008');
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
  });

  it('risk filter narrows rows, resets page and updates the URL', async () => {
    const r = renderWithProviders({ path: '/suppliers?page=2' });
    await screen.findByText('Page 2 of 2');
    await userEvent.selectOptions(screen.getByLabelText('Risk level'), 'HIGH');
    await waitFor(() => expect(rowsOf()).toHaveLength(2));
    expect(query(r).get('risk_level')).toBe('HIGH');
    expect(query(r).has('page')).toBe(false);
    expect(screen.getByText('2 suppliers')).toBeInTheDocument();
  });

  it('debounces search and narrows results', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const r = renderWithProviders({ path: '/suppliers' });
      await screen.findByRole('link', { name: PUNE });
      await user.type(screen.getByLabelText('Search'), 'pune');
      expect(query(r).has('search')).toBe(false);
      await act(async () => {
        vi.advanceTimersByTime(300);
      });
      await waitFor(() => expect(rowsOf()).toHaveLength(1));
      expect(query(r).get('search')).toBe('pune');
    } finally {
      vi.useRealTimers();
    }
  });

  it('clamps an out-of-range page to the last page', async () => {
    const r = renderWithProviders({ path: '/suppliers?page=9' });
    await screen.findByText('Page 2 of 2');
    expect(query(r).get('page')).toBe('2');
    await waitFor(() => expect(rowsOf()).toHaveLength(2));
  });

  it('shows empty state for no match and Clear restores list', async () => {
    const r = renderWithProviders({ path: '/suppliers' });
    await screen.findByRole('link', { name: PUNE });
    await userEvent.type(screen.getByLabelText('Search'), 'zzz');
    expect(await screen.findByText('No suppliers match these filters')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(await screen.findByRole('link', { name: PUNE })).toBeInTheDocument();
    expect(query(r).has('search')).toBe(false);
    expect(screen.getByLabelText('Search')).toHaveValue('');
  });

  it('shows error state and retries', async () => {
    let fail = true;
    server.use(
      http.get('*/suppliers', () =>
        fail
          ? HttpResponse.json({ error: { code: 'INTERNAL', message: 'boom', details: {} } }, { status: 500 })
          : HttpResponse.json({ count: suppliers.length, next: null, previous: null, results: suppliers }),
      ),
    );
    renderWithProviders({ path: '/suppliers' });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Something went wrong');
    fail = false;
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('link', { name: PUNE })).toBeInTheDocument();
  });

  it('paginates with Prev/Next and page_size', async () => {
    const r = renderWithProviders({ path: '/suppliers?page_size=5' });
    await screen.findByText('Page 1 of 3');
    expect(screen.getByRole('button', { name: 'Prev' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Page 2 of 3');
    expect(query(r).get('page')).toBe('2');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Page 3 of 3');
    await waitFor(() => expect(rowsOf()).toHaveLength(2));
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Prev' }));
    await screen.findByText('Page 2 of 3');
  });
});
