import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ApiError } from '@/api/client';
import { AuthProvider } from '@/auth/AuthProvider';
import { ThemeProvider } from '@/theme/ThemeProvider';

/** Retry once, and never on 4xx ApiErrors. */
// eslint-disable-next-line react-refresh/only-export-components -- pure helper kept beside the provider; exported for unit tests
export function shouldRetry(count: number, err: unknown): boolean {
  return !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 1;
}

// eslint-disable-next-line react-refresh/only-export-components -- factory kept beside the provider; exported for tests
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: shouldRetry, staleTime: 30_000, refetchOnWindowFocus: false } },
  });
}

const queryClient = createQueryClient();

/** App-wide providers (everything except the router). Reused by tests. */
export function Providers({ children, client = queryClient }: { children: ReactNode; client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <AuthProvider>{children}</AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
