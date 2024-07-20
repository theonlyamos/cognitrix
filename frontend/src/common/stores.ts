import { writable } from 'svelte/store';
import { BACKEND_URI } from './utils';
import type { SSEMessage, SSEState } from './interfaces';

const sseUrl = new URL(BACKEND_URI + '/agents/sse')
const chatUrl = new URL(BACKEND_URI + '/agents/chat')
const websocketUrl = new URL(BACKEND_URI.replace('http', 'ws')).origin + '/ws';

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

  function connect(): void {
      eventSource = new EventSource(sseUrl);

      eventSource.onopen = () => {
          update(state => ({ ...state, event: 'open', connected: true, error: null }));
      };

      eventSource.onerror = (error: Event) => {
          update(state => ({ ...state, event: 'error', connected: false, error: error }));
      };

      eventSource.onmessage = (event: MessageEvent) => {
          console.log(event)
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

export const webSocketStore = createWebSocketStore();
export const sseStore = createSSEStore();