import type { RiskAnalysis, RiskCaseState } from '@/api/types';
import { AnalysisPanel } from '@/features/risk/analysis';

export interface AnalysisSlotProps {
  caseId: string;
  status: RiskCaseState;
  analysis: RiskAnalysis | null;
}

export function AnalysisSlot({ status, analysis }: AnalysisSlotProps) {
  return (
    <section aria-labelledby="analysis-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="analysis-heading" className="mb-3 text-base font-semibold text-text">
        AI risk analysis
      </h2>
      <div data-testid="analysis-slot">
        <AnalysisPanel analysis={analysis} status={status} />
      </div>
    </section>
  );
}
