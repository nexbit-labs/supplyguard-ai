import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { login, logout, onLogout } from '@/api/client';
import { clearTokens, hasSession } from '@/api/tokens';
import { getMe } from '@/api/endpoints';
import type { Me, Role } from '@/api/types';

export type AuthState =
  | { status: 'loading'; me?: undefined }
  | { status: 'anonymous'; me?: undefined }
  | { status: 'authenticated'; me: Me };

export interface AuthContextValue {
  status: AuthState['status'];
  me?: Me;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => void;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>(() => ({ status: hasSession() ? 'loading' : 'anonymous' }));

  // Restore session on mount.
  useEffect(() => {
    if (!hasSession()) return;
    let cancelled = false;
    getMe()
      .then((me) => {
        if (!cancelled) setState({ status: 'authenticated', me });
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'anonymous' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Refresh failure / explicit logout -> anonymous.
  useEffect(
    () =>
      onLogout(() => {
        setState({ status: 'anonymous' });
        queryClient.clear();
      }),
    [queryClient],
  );

  const signIn = useCallback(async (username: string, password: string) => {
    await login(username, password);
    try {
      const me = await getMe();
      setState({ status: 'authenticated', me });
    } catch (e) {
      clearTokens();
      throw e;
    }
  }, []);

  const signOut = useCallback(() => {
    logout();
    queryClient.clear();
    setState({ status: 'anonymous' });
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status: state.status,
      me: state.me,
      signIn,
      signOut,
      hasRole: (...roles: Role[]) => !!state.me && roles.includes(state.me.role),
    }),
    [state, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- hook intentionally co-located with its provider
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
