import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getDashboardSummary,
  getMe,
  getRiskCase,
  getSupplier,
  listRiskCases,
  listSuppliers,
  simulateDisruption,
} from './endpoints';
import type { RiskCaseFilters, SimulateScenario, SupplierFilters } from './types';

export const queryKeys = {
  me: ['me'] as const,
  suppliers: (filters: SupplierFilters = {}) => ['suppliers', filters] as const,
  supplier: (id: string) => ['supplier', id] as const,
  dashboard: ['dashboard', 'summary'] as const,
  riskCases: (filters: RiskCaseFilters = {}) => ['risk-cases', filters] as const,
  riskCase: (id: string) => ['risk-case', id] as const,
};

export const useMe = (enabled = true) =>
  useQuery({ queryKey: queryKeys.me, queryFn: getMe, enabled, staleTime: 5 * 60_000 });

export const useSuppliers = (filters: SupplierFilters = {}) =>
  useQuery({ queryKey: queryKeys.suppliers(filters), queryFn: () => listSuppliers(filters) });

export const useSupplier = (id: string | undefined) =>
  useQuery({
    queryKey: queryKeys.supplier(id ?? ''),
    queryFn: () => getSupplier(id as string),
    enabled: !!id,
  });

export const useDashboardSummary = () =>
  useQuery({ queryKey: queryKeys.dashboard, queryFn: getDashboardSummary });

export interface RiskCasesQueryOptions {
  /** Poll interval in ms; polling pauses while the tab is hidden. */
  refetchInterval?: number | false;
  /** Keep showing the previous page/filter result while the next one loads. */
  keepPrevious?: boolean;
}

export const useRiskCases = (filters: RiskCaseFilters = {}, options: RiskCasesQueryOptions = {}) =>
  useQuery({
    queryKey: queryKeys.riskCases(filters),
    queryFn: () => listRiskCases(filters),
    refetchInterval: options.refetchInterval,
    placeholderData: options.keepPrevious ? keepPreviousData : undefined,
  });

export const useRiskCase = (id: string | undefined) =>
  useQuery({
    queryKey: queryKeys.riskCase(id ?? ''),
    queryFn: () => getRiskCase(id as string),
    enabled: !!id,
  });

export const useSimulateDisruption = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scenario: SimulateScenario | string) => simulateDisruption(scenario),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['risk-cases'] });
      void qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
};
