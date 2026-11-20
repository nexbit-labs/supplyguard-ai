import { Skeleton } from '@/components/ui';

export function CaseDetailSkeleton() {
  return (
    <div role="status" aria-label="Loading risk case" className="space-y-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-4 w-96 max-w-full" />
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Skeleton className="h-40" />
          <Skeleton className="h-48" />
        </div>
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}
