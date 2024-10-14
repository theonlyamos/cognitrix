import { writable } from 'svelte/store';
import { API_BACKEND_URI } from './constants';
import type { SSEMessage, SSEState, User } from './interfaces';
import { navigate } from 'svelte-routing';

const sseUrl = new URL(API_BACKEND_URI + '/agents/sse')
const chatUrl = new URL(API_BACKEND_URI + '/agents/chat')
const websocketUrl = new URL(API_BACKEND_URI.replace('http', 'ws')).origin + '/ws';

function createWebSocketStore() {
  const { subscribe, set } = writable<any>(null);

  let socket: WebSocket | null = null;

  function connect() {
    socket = new WebSocket(websocketUrl);

    socket.onopen = ()=>{
      console.log('WebSocket connection established');
      set({socket, type: 'open', data: null});
    }

    socket.onmessage = (event: MessageEvent) => {
      set({socket, type: 'message', data: event.data});
    };

    socket.onclose = () => {
      console.log('WebSocket connection closed');
      socket = null;
    };

    socket.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }


  function send(message: string) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(message);
    }
  }

  function close() {
    if (socket) {
      socket.close();
    }
  }

  return {
    subscribe,
    connect,
    send,
    close,
  };
}

function createSSEStore() {
  const { subscribe, set, update } = writable<SSEState>({
      event: 'close',
      message: null,
      connected: false,
      error: null
  });

  let eventSource: EventSource | null = null;

  function connect(sseUrl: string): void {
      eventSource = new EventSource(sseUrl);

      eventSource.onopen = () => {
          update(state => ({ ...state, event: 'open', connected: true, error: null }));
      };

      eventSource.onerror = (error: Event) => {
          update(state => ({ ...state, event: 'error', connected: false, error: error }));
      };

      eventSource.onmessage = (event: MessageEvent) => {
          try {
              const data: SSEMessage = JSON.parse(event.data);
              update(state => ({
                  ...state,
                  event: 'message',
                  message: data
              }));
          } catch (error) {
              console.error('Error parsing SSE message:', error);
          }
      };
  }

  function disconnect(): void {
      if (eventSource) {
          eventSource.close();
          update(state => ({ ...state, connected: false }));
      }
  }

  function sendMessage(message: string): void {
      fetch(chatUrl, {
          method: 'POST',
          headers: {
              'Content-Type': 'application/json',
          },
          body: JSON.stringify({ message: message }),
      }).then(response => response.json())
        .then(data => {})
        .catch(error => console.error('Error:', error));
  }

  return {
      subscribe,
      connect,
      disconnect,
      sendMessage
  };
}

function createUserStore() {
  const { subscribe, set, update } = writable<User | null>(null);

  return {
    subscribe,
    login: (user: User, token: string) => {
      localStorage.setItem('token', token);
      set(user);
    },
    logout: () => {
      localStorage.removeItem('token');
      set(null);
      navigate('/'); // Redirect to home page after logout
    },
    checkAuth: async () => {
      const token = localStorage.getItem('token');
      if (token) {
        try {
          const response = await fetch(`${API_BACKEND_URI}/auth/user`, {
            headers: {
              'Authorization': `Bearer ${token}`
            }
          });
          if (response.ok) {
            const user: User = await response.json();
            set(user);
          } else {
            localStorage.removeItem('token');
            set(null);
            navigate('/');
          }
        } catch (error) {
          console.error('Error checking authentication:', error);
          localStorage.removeItem('token');
          set(null);
          navigate('/');
        }
      } else {
        navigate('/');
      }
    }
  };
}

export const webSocketStore = createWebSocketStore();
export const sseStore = createSSEStore();
export const userStore = createUserStore();