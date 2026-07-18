import React, { createContext, useContext, useState, useCallback } from 'react';

export type ToolStatus = 'running' | 'done' | 'error' | 'stopped';

export interface ToolArtifact {
  id: string;
  mime_type: string;
  origin?: 'uploaded' | 'generated';
  filename?: string;
  width?: number;
  height?: number;
}

export interface ToolUse {
  /** tool_call_id — pairs the started chip with its completion. */
  id?: string;
  name: string;
  status: ToolStatus;
  /** Tool-call arguments (pretty JSON string), shown when the chip expands. */
  params?: string;
  /** Tool output preview (may be truncated), shown when the chip expands. */
  result?: string;
  artifacts?: ToolArtifact[];
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  timestamp?: string;
  /** Session-owned images attached to this user turn. */
  artifacts?: ToolArtifact[];
  /** Tool chips for a `role: 'tool'` row (the agent's tool activity). */
  tools?: ToolUse[];
}

interface SessionContextType {
  messages: ChatMessage[];
  currentAgentId: string | null;
  isStreaming: boolean;
  addMessage: (role: ChatMessage['role'], content: string) => void;
  addArtifactsToLastUser: (artifacts: ToolArtifact[]) => void;
  appendToLastMessage: (content: string) => void;
  /** A tool started: append a running chip to the current tool row, or open one. */
  addToolCall: (name: string, opts?: { id?: string; params?: string }) => void;
  /** A tool finished: flip its most-recent running chip to done/error + result. */
  resolveToolCall: (name: string, status: 'done' | 'error', opts?: { id?: string; result?: string; artifacts?: ToolArtifact[] }) => void;
  /** Mark every still-running tool in the active transcript as user-stopped. */
  stopRunningTools: () => void;
  /** Mark every still-running tool as failed when the turn terminates in error. */
  failRunningTools: (result?: string) => void;
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

  const addArtifactsToLastUser = useCallback((artifacts: ToolArtifact[]) => {
    if (!artifacts.length) return;
    setMessagesState((previous) => {
      for (let index = previous.length - 1; index >= 0; index -= 1) {
        const message = previous[index];
        if (message.role !== 'user') continue;
        const byId = new Map((message.artifacts || []).map((artifact) => [artifact.id, artifact]));
        for (const artifact of artifacts) byId.set(artifact.id, artifact);
        const updated = [...previous];
        updated[index] = { ...message, artifacts: [...byId.values()] };
        return updated;
      }
      return previous;
    });
  }, []);

  // A tool started: append a running chip. Merge into the last row if it's
  // already a tool row (consecutive tool rounds with no text between), else
  // open a new row — a text reply in between naturally splits the rows.
  const addToolCall = useCallback((name: string, opts?: { id?: string; params?: string }) => {
    setMessagesState(prev => {
      const chip: ToolUse = { id: opts?.id, name, status: 'running', params: opts?.params };
      const last = prev[prev.length - 1];
      if (last && last.role === 'tool') {
        return [...prev.slice(0, -1), { ...last, tools: [...(last.tools || []), chip] }];
      }
      return [...prev, {
        id: `tool-${Date.now()}-${prev.length}`,
        role: 'tool',
        content: '',
        tools: [chip],
        timestamp: new Date().toISOString(),
      }];
    });
  }, []);

  // A tool finished: flip the matching running chip to done/error and attach its
  // result. Pair by tool_call_id when present, else by name (most recent running).
  const resolveToolCall = useCallback((name: string, status: 'done' | 'error', opts?: { id?: string; result?: string; artifacts?: ToolArtifact[] }) => {
    setMessagesState(prev => {
      for (let i = prev.length - 1; i >= 0; i--) {
        const m = prev[i];
        if (m.role !== 'tool' || !m.tools) continue;
        let hit = -1;
        for (let j = m.tools.length - 1; j >= 0; j--) {
          const t = m.tools[j];
          if (t.status !== 'running') continue;
          if (opts?.id ? t.id === opts.id : t.name === name) { hit = j; break; }
        }
        if (hit === -1) continue;
        const tools = m.tools.map((t, j) => (j === hit ? { ...t, status, result: opts?.result, artifacts: opts?.artifacts } : t));
        const copy = [...prev];
        copy[i] = { ...m, tools };
        return copy;
      }
      if (status !== 'error' && !opts?.artifacts?.length) return prev;
      // Preserve terminal errors and artifact-bearing completions even if a
      // reconnect caused the matching `started` event to be missed.
      const chip: ToolUse = {
        id: opts?.id,
        name,
        status,
        result: opts?.result,
        artifacts: opts?.artifacts,
      };
      const last = prev[prev.length - 1];
      if (last && last.role === 'tool') {
        return [...prev.slice(0, -1), { ...last, tools: [...(last.tools || []), chip] }];
      }
      return [...prev, {
        id: `tool-${Date.now()}-${prev.length}`,
        role: 'tool',
        content: '',
        tools: [chip],
        timestamp: new Date().toISOString(),
      }];
    });
  }, []);

  const stopRunningTools = useCallback(() => {
    setMessagesState(prev => prev.map(message => {
      if (!message.tools?.some(tool => tool.status === 'running')) return message;
      return {
        ...message,
        tools: message.tools.map(tool => (
          tool.status === 'running'
            ? { ...tool, status: 'stopped' as const, result: 'Stopped by user.' }
            : tool
        )),
      };
    }));
  }, []);

  const failRunningTools = useCallback((result = 'The turn ended before this tool completed.') => {
    setMessagesState(prev => prev.map(message => {
      if (!message.tools?.some(tool => tool.status === 'running')) return message;
      return {
        ...message,
        tools: message.tools.map(tool => (
          tool.status === 'running'
            ? { ...tool, status: 'error' as const, result }
            : tool
        )),
      };
    }));
  }, []);

  const clearMessages = useCallback(() => {
    setMessagesState([]);
  }, []);

  const setAgentId = useCallback((agentId: string | null) => {
    setCurrentAgentId(agentId);
  }, []);

  return (
    <SessionContext.Provider value={{
      messages,
      currentAgentId,
      isStreaming,
      addMessage,
      addArtifactsToLastUser,
      appendToLastMessage,
      addToolCall,
      resolveToolCall,
      stopRunningTools,
      failRunningTools,
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
