import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

const API_BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL}/api/v1`;

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
}

export interface ToolEvent {
  id: string;
  toolName: string;
  status: 'started' | 'completed' | 'error';
  timestamp: string;
}

interface SessionContextType {
  messages: ChatMessage[];
  currentAgentId: string | null;
  isStreaming: boolean;
  isLoading: boolean;
  toolEvents: ToolEvent[];
  addMessage: (role: ChatMessage['role'], content: string) => void;
  appendToLastMessage: (content: string) => void;
  clearMessages: () => void;
  setMessages: (messages: ChatMessage[]) => void;
  setAgentId: (agentId: string | null) => void;
  setIsStreaming: (isStreaming: boolean) => void;
  loadSession: () => Promise<void>;
}

const SessionContext = createContext<SessionContextType | undefined>(undefined);

const STORAGE_KEY = 'cognitrix_chat_session';

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessagesState] = useState<ChatMessage[]>([]);
  const [currentAgentId, setCurrentAgentId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const { messages: storedMessages, agentId } = JSON.parse(stored);
        if (Array.isArray(storedMessages)) {
          setMessagesState(storedMessages);
          if (agentId) setCurrentAgentId(agentId);
        }
      }
    } catch (err) {
      console.error('Failed to load session from storage:', err);
    }
  }, []);

  // Save to localStorage on messages change
  useEffect(() => {
    try {
      const toStore = JSON.stringify({
        messages,
        agentId: currentAgentId,
      });
      localStorage.setItem(STORAGE_KEY, toStore);
    } catch (err) {
      console.error('Failed to save session to storage:', err);
    }
  }, [messages, currentAgentId]);

  const addMessage = useCallback((role: ChatMessage['role'], content: string) => {
    const newMessage: ChatMessage = {
      id: Date.now().toString(),
      role,
      content,
      timestamp: new Date().toISOString(),
    };
    setMessagesState(prev => [...prev, newMessage]);
  }, []);

  const appendToLastMessage = useCallback((content: string) => {
    setMessagesState(prev => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role !== 'assistant') {
        // Create new assistant message if last was user
        return [...prev, {
          id: Date.now().toString(),
          role: 'assistant',
          content,
          timestamp: new Date().toISOString(),
        }];
      }
      // Append to existing assistant message
      const updated = [...prev];
      updated[updated.length - 1] = {
        ...last,
        content: last.content + content,
      };
      return updated;
    });
  }, []);

  const clearMessages = useCallback(() => {
    setMessagesState([]);
    setToolEvents([]);
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  const setAgentId = useCallback((agentId: string | null) => {
    setCurrentAgentId(agentId);
  }, []);

  const loadSession = useCallback(async () => {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BACKEND_URI}/agents/session`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      
      if (response.ok) {
        const data = await response.json();
        if (data.chat && Array.isArray(data.chat)) {
          const loadedMessages: ChatMessage[] = data.chat.map((msg: { role: string; content: string }, idx: number) => ({
            id: `${Date.now()}-${idx}`,
            role: msg.role as ChatMessage['role'],
            content: msg.content,
          }));
          setMessagesState(loadedMessages);
          if (data.agent_id) setCurrentAgentId(data.agent_id);
        }
      }
    } catch (err) {
      console.error('Failed to load session:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  return (
    <SessionContext.Provider value={{
      messages,
      currentAgentId,
      isStreaming,
      isLoading,
      toolEvents,
      addMessage,
      appendToLastMessage,
      clearMessages,
      setMessages: setMessagesState,
      setAgentId,
      setIsStreaming,
      loadSession,
    }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const context = useContext(SessionContext);
  if (!context) throw new Error('useSession must be used within SessionProvider');
  return context;
}