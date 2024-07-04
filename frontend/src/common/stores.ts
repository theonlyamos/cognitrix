import { writable } from 'svelte/store';
import { BACKEND_URI } from './utils';

function createWebSocketStore(url: string) {
  const { subscribe, set } = writable<any>(null);

  let socket: WebSocket | null = null;

  function connect() {
    socket = new WebSocket(url);

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

const websocketUrl = new URL(BACKEND_URI.replace('http', 'ws')).origin + '/ws';
export const webSocketStore = createWebSocketStore(websocketUrl);