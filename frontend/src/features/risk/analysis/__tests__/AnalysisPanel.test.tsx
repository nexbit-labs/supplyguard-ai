import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { RiskAnalysis, RiskCaseState } from '../../../../api/types';
import { riskCaseDetail45 } from '../../../../mocks/fixtures';
import { AnalysisPanel } from '..';

const fixture = riskCaseDetail45.analysis as RiskAnalysis;

function ui(analysis: RiskAnalysis | null, status?: RiskCaseState) {
  return (
    <MemoryRouter>
      <AnalysisPanel analysis={analysis} status={status} />
    </MemoryRouter>
  );
}
const group = (name: string) => screen.getByRole('region', { name });

describe('AnalysisPanel', () => {
  it('renders every group with counts and tags', () => {
    render(ui(fixture));
    const f = group('Facts');
    expect(within(f).getByRole('heading', { level: 3 })).toHaveTextContent('Facts (3)');
    expect(f.querySelectorAll('[data-evidence="FACT"]')).toHaveLength(3);
    expect(group('Estimates')).toHaveTextContent('Estimates (1)');
    expect(group('Estimates').querySelectorAll('[data-evidence="ESTIMATE"]')).toHaveLength(1);
    expect(group('Assumptions')).toHaveTextContent('Assumptions (2)');
    expect(group('Assumptions').querySelectorAll('[data-evidence="ASSUMPTION"]')).toHaveLength(2);
    expect(group('Missing data')).toHaveTextContent('Missing data (2)');
    expect(group('Missing data').querySelectorAll('[data-evidence="MISSING_DATA"]')).toHaveLength(2);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '92');
    expect(screen.getByText(/Confidence: MEDIUM/)).toBeInTheDocument();
  });

  it('shows Basis on estimates', () => {
    render(ui(fixture));
    expect(group('Estimates')).toHaveTextContent('Basis: 48h outage plus 2 days restart and transit time');
  });

  it('shows missing data visibly', () => {
    render(ui(fixture));
    const m = group('Missing data');
    expect(m).toHaveTextContent('The AI did not have:');
    expect(m).toHaveTextContent('Supplier capacity after restart not provided');
    expect(m).toBeVisible();
  });

  it('handles null analysis', () => {
    const { unmount } = render(ui(null, 'ANALYZING'));
    expect(screen.getByText('AI analysis in progress…')).toBeInTheDocument();
    unmount();
    render(ui(null, 'CLOSED'));
    expect(screen.getByText('No analysis available')).toBeInTheDocument();
  });

  it('labels null evidence value as missing', () => {
    render(
      ui({
        ...fixture,
        evidence: [{ source: 'ERP', entity: 'SUP-1008', field: 'capacity', value: null }],
      }),
    );
    const row = screen.getByRole('row', { name: /capacity/ });
    expect(row).toHaveTextContent('missing');
    expect(row.querySelector('[data-evidence="MISSING_DATA"]')).not.toBeNull();
    expect(within(row).getByRole('link', { name: 'SUP-1008' })).toHaveAttribute('href', '/suppliers/SUP-1008');
  });

  it('links supplier chips only', () => {
    render(ui(fixture));
    const sec = screen.getByRole('region', { name: 'Affected entities' });
    expect(within(sec).getByRole('link', { name: 'SUP-1008' })).toHaveAttribute('href', '/suppliers/SUP-1008');
    expect(within(sec).queryByRole('link', { name: 'PO-1001' })).toBeNull();
  });

  it('renders empty groups', () => {
    render(
      ui({
        ...fixture,
        facts: [],
        estimates: [],
        assumptions: [],
        missing_data: [],
        evidence: [],
        affected_entities: [],
        confidence: 'LOW',
      }),
    );
    expect(group('Facts')).toHaveTextContent('None reported');
    expect(group('Estimates')).toHaveTextContent('None reported');
    expect(group('Assumptions')).toHaveTextContent('None reported');
    expect(group('Missing data')).toHaveTextContent('No data gaps reported');
  });
});
