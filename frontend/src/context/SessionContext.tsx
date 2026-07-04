import React, { createContext, useContext, useState, useCallback } from 'react';

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
  toolEvents: ToolEvent[];
  addMessage: (role: ChatMessage['role'], content: string) => void;
  appendToLastMessage: (content: string) => void;
  clearMessages: () => void;
  setMessages: (messages: ChatMessage[]) => void;
  setAgentId: (agentId: string | null) => void;
  setIsStreaming: (isStreaming: boolean) => void;
}

const SessionContext = createContext<SessionContextType | undefined>(undefined);

// Messages are NOT persisted locally: conversations live server-side (Session
// rows) and Home restores the active one on load. A localStorage copy would
// resurrect stale threads and fight that restore.
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessagesState] = useState<ChatMessage[]>([]);
  const [currentAgentId, setCurrentAgentId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);

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
  }, []);

  const setAgentId = useCallback((agentId: string | null) => {
    setCurrentAgentId(agentId);
  }, []);

  return (
    <SessionContext.Provider value={{
      messages,
      currentAgentId,
      isStreaming,
      toolEvents,
      addMessage,
      appendToLastMessage,
      clearMessages,
      setMessages: setMessagesState,
      setAgentId,
      setIsStreaming,
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
