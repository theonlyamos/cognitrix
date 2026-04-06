import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

const API_BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL}/api/v1`;

interface User {
  id?: string;
  name: string;
  email: string;
}

interface WebSocketState {
  socket: WebSocket | null;
  type: string;
  data: string | null;
}

interface UserContextType {
  user: User | null;
  isLoading: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

interface WebSocketContextType {
  wsState: WebSocketState;
  sendMessage: (message: string) => void;
  connect: () => void;
  disconnect: () => void;
}

const UserContext = createContext<UserContextType | undefined>(undefined);
const WebSocketContext = createContext<WebSocketContextType | undefined>(undefined);

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
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.ok) {
          const user: User = await response.json();
          setUser(user);
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

  return (
    <UserContext.Provider value={{ user, isLoading, login, logout, checkAuth }}>
      {children}
    </UserContext.Provider>
  );
}

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [wsState, setWsState] = useState<WebSocketState>({
    socket: null,
    type: 'close',
    data: null
  });

  const connect = useCallback(() => {
    try {
      const websocketUrl = API_BACKEND_URI.replace('http', 'ws').split('/api')[0] + '/ws';
      const socket = new WebSocket(websocketUrl);

      socket.onopen = () => {
        console.log('WebSocket connection established');
        setWsState({ socket, type: 'open', data: null });
      };

      socket.onmessage = (event: MessageEvent) => {
        setWsState({ socket, type: 'message', data: event.data });
      };

      socket.onclose = () => {
        console.log('WebSocket connection closed');
        setWsState({ socket: null, type: 'close', data: null });
      };

      socket.onerror = (error) => {
        console.error('WebSocket error:', error);
      };
    } catch (err) {
      console.error('Failed to connect WebSocket:', err);
    }
  }, []);

  const sendMessage = useCallback((message: string) => {
    if (wsState.socket && wsState.socket.readyState === WebSocket.OPEN) {
      wsState.socket.send(message);
    }
  }, [wsState.socket]);

  const disconnect = useCallback(() => {
    if (wsState.socket) {
      wsState.socket.close();
    }
  }, [wsState.socket]);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  return (
    <WebSocketContext.Provider value={{ wsState, sendMessage, connect, disconnect }}>
      {children}
    </WebSocketContext.Provider>
  );
}

export function useUser() {
  const context = useContext(UserContext);
  if (!context) throw new Error('useUser must be used within UserProvider');
  return context;
}

export function useWebSocket() {
  const context = useContext(WebSocketContext);
  if (!context) throw new Error('useWebSocket must be used within WebSocketProvider');
  return context;
}