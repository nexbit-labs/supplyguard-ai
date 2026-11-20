import type { TrendPoint } from '@/api/types';

export const hasTrend = (t: TrendPoint[] | undefined) => !!t && t.length > 0;
