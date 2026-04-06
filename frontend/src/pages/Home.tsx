import { useUser } from '@/context/AppContext';
import { useSession } from '@/context/SessionContext';
import { useSSE } from '@/hooks/useSSE';
import { useState, useRef, useEffect, useCallback } from 'react';

const API_BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL}/api/v1`;

export default function Home() {
  const { user } = useUser();
  const { messages, addMessage, appendToLastMessage, setIsStreaming, toolEvents } = useSession();
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Stable callback for SSE events - no dependencies to prevent reconnects
  const handleSSEEvent = useCallback((event: { type: string; content?: string; action?: string }) => {
    if (event.type === 'generate' && event.content) {
      appendToLastMessage(event.content);
    } else if (event.type === 'chat_history' && event.content) {
      // Handle historical messages - already loaded via loadSession
    } else if (event.type === 'chat') {
      // Direct chat responses
      if (event.action === 'get' && event.content) {
        // This is a response to add
      }
    }
  }, [appendToLastMessage]);

  // Stable callback for tool events
  const handleToolEvent = useCallback((toolName: string, status: string) => {
    console.log(`Tool: ${toolName} - ${status}`);
  }, []);

  // Stable callback for errors
  const handleSSEError = useCallback((err: Error) => {
    console.error('SSE error:', err);
  }, []);

  // SSE connection - handlers are stable so no reconnects on render
  const { isConnected, error, reconnect } = useSSE({
    onMessage: handleSSEEvent,
    onTool: handleToolEvent,
    onError: handleSSEError,
  });

  const sendMessage = async () => {
    if (!input.trim() || isLoading) return;
    
    addMessage('user', input);
    const messageToSend = input;
    setInput('');
    setIsLoading(true);
    setIsStreaming(true);

    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BACKEND_URI}/agents/chat`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ message: messageToSend }),
      });
      
      if (!response.ok) {
        throw new Error('Failed to send message');
      }
      
      // Response is sent via SSE, we just wait for streaming
      // The SSE hook will append content to the last message
    } catch (err) {
      addMessage('assistant', 'Unable to connect. Please check your internet connection and try again.');
    } finally {
      setIsLoading(false);
      setIsStreaming(false);
    }
  };

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [input, isLoading]);

  return (
    <div className="flex-1 flex flex-col h-full bg-gray-900">
      {/* Header */}
      <div className="border-b border-gray-800 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-white">Welcome back, {user?.name?.split(' ')[0] || 'User'}</h1>
            <p className="text-gray-400 text-sm mt-1">How can I help you today?</p>
          </div>
          <div className="flex items-center gap-4">
            {/* Tool Events Indicator */}
            {toolEvents.length > 0 && (
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <span className="animate-pulse">⚙️</span>
                <span>{toolEvents[toolEvents.length - 1].toolName}</span>
              </div>
            )}
            {/* Connection Status */}
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-yellow-500'}`}></span>
              <span className="text-xs text-gray-500">{isConnected ? 'Connected' : 'Disconnected'}</span>
              {!isConnected && (
                <button 
                  onClick={reconnect}
                  className="text-xs text-blue-400 hover:text-blue-300 ml-2"
                >
                  Reconnect
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 bg-blue-600/20 rounded-full flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-white mb-2">Start a conversation</h3>
            <p className="text-gray-400 max-w-md">
              Send a message to start chatting with your AI assistant. You can ask questions, get help with tasks, or just chat.
            </p>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[70%] rounded-2xl px-4 py-3 ${
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-800 text-gray-100'
                }`}
              >
                <p className="whitespace-pre-wrap">{msg.content}</p>
                {msg.timestamp && (
                  <p className={`text-xs mt-2 ${msg.role === 'user' ? 'text-blue-200' : 'text-gray-500'}`}>
                    {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </p>
                )}
              </div>
            </div>
          ))
        )}
        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 rounded-2xl px-4 py-3">
              <div className="flex items-center gap-2 text-gray-400">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span className="text-sm">Thinking...</span>
              </div>
            </div>
          </div>
        )}
        {error && (
          <div className="flex justify-center">
            <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-2">
              <p className="text-sm text-red-400">Connection error. <button onClick={reconnect} className="underline">Retry</button></p>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-800 px-6 py-4">
        <div className="flex gap-3 max-w-4xl mx-auto">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your message..."
            disabled={isLoading}
            className="flex-1 px-4 py-3 bg-gray-800 border border-gray-700 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all disabled:opacity-50"
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || isLoading}
            className="px-6 py-3 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-xl transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {isLoading ? (
              <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            )}
          </button>
        </div>
        <p className="text-center text-xs text-gray-500 mt-2">
          Press <kbd className="px-1.5 py-0.5 bg-gray-800 rounded text-gray-400">Enter</kbd> to send, <kbd className="px-1.5 py-0.5 bg-gray-800 rounded text-gray-400">Shift + Enter</kbd> for new line
        </p>
      </div>
    </div>
  );
}