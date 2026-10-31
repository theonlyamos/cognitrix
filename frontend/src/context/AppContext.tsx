import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

const API_BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL ?? ''}/api/v1`;

interface User {
  id?: string;
  name: string;
  email: string;
}

interface UserContextType {
  user: User | null;
  isLoading: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

const UserContext = createContext<UserContextType | undefined>(undefined);

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const login = useCallback((user: User, token: string) => {
    localStorage.setItem('token', token);
    setUser(user);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('token');
    setUser(null);
  }, []);

  const checkAuth = useCallback(async () => {
    setIsLoading(true);
    const token = localStorage.getItem('token');
    if (token) {
      try {
        const response = await fetch(`${API_BACKEND_URI}/auth/user`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (response.ok) {
          setUser(await response.json());
        } else {
          localStorage.removeItem('token');
          setUser(null);
        }
      } catch {
        localStorage.removeItem('token');
        setUser(null);
      }
    }
    setIsLoading(false);
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

  return (
    <UserContext.Provider value={{ user, isLoading, login, logout, checkAuth }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser() {
  const context = useContext(UserContext);
  if (!context) throw new Error('useUser must be used within UserProvider');
  return context;
}
