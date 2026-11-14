import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { AUTH_EXPIRED_EVENT, clearToken, getToken, refreshTokenIfOld, restoreToken, setToken } from '@/lib/session';

const API_BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL ?? ''}/api/v1`;

/** How often a signed-in app checks whether its token is due a refresh. */
const REFRESH_CHECK_MS = 60 * 60 * 1000;

interface User {
  id?: string;
  name: string;
  email: string;
  /** May manage the people on a shared server. */
  is_admin?: boolean;
}

interface UserContextType {
  user: User | null;
  isLoading: boolean;
  /** Signed in, but the server (the desktop engine) isn't answering yet. */
  waitingForServer: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

const UserContext = createContext<UserContextType | undefined>(undefined);

type Verdict = { kind: 'user'; user: User } | { kind: 'rejected' } | { kind: 'unreachable' };

async function verify(token: string): Promise<Verdict> {
  try {
    const response = await fetch(`${API_BACKEND_URI}/auth/user`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (response.ok) return { kind: 'user', user: await response.json() };
    // Only the server saying no means the sign-in is over. A 5xx or a proxy
    // error while the engine starts or restarts says nothing about it.
    if (response.status === 401 || response.status === 403) return { kind: 'rejected' };
  } catch {
    /* engine down, starting, or restarting */
  }
  return { kind: 'unreachable' };
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [waitingForServer, setWaitingForServer] = useState(false);
  // Each check gets a number; an older check that's still retrying stops.
  const checkRun = useRef(0);

  const login = useCallback((user: User, token: string) => {
    setToken(token);
    setUser(user);
  }, []);

  const logout = useCallback(() => {
    checkRun.current += 1;
    clearToken();
    setUser(null);
  }, []);

  const checkAuth = useCallback(async () => {
    const run = ++checkRun.current;
    setIsLoading(true);
    const token = await restoreToken();
    if (!token) {
      if (run === checkRun.current) {
        setUser(null);
        setIsLoading(false);
      }
      return;
    }
    // Keep the sign-in while the engine can't be reached and try again:
    // signing out because the server was briefly down is what used to make
    // the desktop app ask for a password after every restart.
    for (let attempt = 0; run === checkRun.current; attempt += 1) {
      const verdict = await verify(token);
      if (run !== checkRun.current) return;
      if (verdict.kind === 'user') {
        setUser(verdict.user);
        setWaitingForServer(false);
        setIsLoading(false);
        void refreshTokenIfOld(API_BACKEND_URI);
        return;
      }
      if (verdict.kind === 'rejected') {
        if (getToken() === token) clearToken();
        setUser(null);
        setWaitingForServer(false);
        setIsLoading(false);
        return;
      }
      if (attempt >= 1) setWaitingForServer(true);
      await wait(Math.min(1000 * 2 ** attempt, 5000));
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Another window of the app (the desktop quick ask, a second tab) signed
  // in or out: follow it.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === 'token' || event.key === null) void checkAuth();
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [checkAuth]);

  // A request was refused with 401: see whether the sign-in really ended,
  // without blanking the page while checking.
  useEffect(() => {
    const onExpired = () => {
      const token = getToken();
      if (!token) return;
      void verify(token).then((verdict) => {
        if (verdict.kind === 'rejected' && getToken() === token) {
          clearToken();
          setUser(null);
        }
      });
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  // Keep a long-open app's token fresh.
  useEffect(() => {
    if (!user) return;
    const id = setInterval(() => void refreshTokenIfOld(API_BACKEND_URI), REFRESH_CHECK_MS);
    return () => clearInterval(id);
  }, [user]);

  return (
    <UserContext.Provider value={{ user, isLoading, waitingForServer, login, logout, checkAuth }}>
      {children}
    </UserContext.Provider>
  );
}

/** The signed-in user, or null outside the provider (pages rendered alone in tests). */
export function useOptionalUser() {
  return useContext(UserContext)?.user ?? null;
}

export function useUser() {
  const context = useContext(UserContext);
  if (!context) throw new Error('useUser must be used within UserProvider');
  return context;
}
