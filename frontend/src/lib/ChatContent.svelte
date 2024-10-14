<script lang="ts">
  import { afterUpdate, beforeUpdate, onDestroy } from "svelte";
  import Loader from "./Loader.svelte";
  import type { MessageInterface } from "../common/interfaces";
  import MessageComponent from "./Message.svelte";
  import Inputbar from "./Inputbar.svelte";
  import { deleteChat, getSession } from "../common/utils";
  import type { SessionInterface } from "../common/interfaces";
  import { webSocketStore } from "../common/stores";
  import type { Unsubscriber } from "svelte/motion";

  export let session_id: string;
  export let showClearButton: boolean = true;
  export let inputPlaceholder: string = "Enter your message...";
    
  let messages: MessageInterface[] = [];
  let loading: boolean = true;
  let container: HTMLElement;
  let autoscroll = false;
  let unsubscribe: Unsubscriber | null = null;
  let socket: WebSocket;
  let streaming_response: boolean = false;
  let agentName: string = "Assistant";

  beforeUpdate(() => {
    if (container) {
      const scrollableDistance =
        container.scrollHeight - container.offsetHeight;
      autoscroll = container.scrollTop > scrollableDistance - 20;
    }
  });

  afterUpdate(() => {
    if (autoscroll) {
      container.scrollTo(0, container.scrollHeight);
    }
  });

  const onFileSelect = (event: Event) => {
    console.log(event)
  }

  const loadSession = async (sessionId: string) => {
    loading = true
    try {
      const response = await getSession(sessionId) as SessionInterface
      session_id = response.id
      messages = response.chat as MessageInterface[]
    } catch (error) {
      console.error("Error loading session:", error);
    } finally {
      loading = false
    }
  }

  const startWebSocketConnection = () => {
    unsubscribe = webSocketStore.subscribe(
      (event: { socket: WebSocket; type: string; data?: any }) => {
        if (event !== null) {
          socket = event.socket;
          if (event.type === "message") {
            loading = false;
            let data = JSON.parse(event.data);
            
            if (data.type === "chat_message") {
              console.log("Received chat message:", data);
              console.log("Current messages:", messages);
              
              const new_message = {
                role: agentName,
                type: 'text',
                content: data.content,
              };

              if (!streaming_response) {
                console.log("Not streaming, adding new message");
                messages = [...messages, new_message];
              } else {
                console.log("Streaming, updating last message");
                if (data.complete) {
                  console.log("Stream complete, setting final content");
                  messages = messages.map((msg, index) => 
                    index === messages.length - 1 ? {...msg, content: new_message.content} : msg
                  );
                } else {
                  console.log("Stream ongoing, appending content");
                  messages = messages.map((msg, index) => 
                    index === messages.length - 1 ? {...msg, content: msg.content + new_message.content} : msg
                  );
                }
              }

              console.log("Updated messages:", messages);
              streaming_response = true;
            }
          }
        }
      }
    );
  };

  const sendAudioChunk = (chunk: Blob) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(chunk);
    }
  };

  const handleAudioChunk = (event: CustomEvent) => {
    sendAudioChunk(event.detail.chunk);
  };

  const handleAudioComplete = async (event: CustomEvent) => {
    // You can add any cleanup or final processing here
    console.log("Audio recording complete");
  };

  const sendMessage = async (query: string) => {
    console.log("Sending message, current messages:", messages);
    messages = [
      ...messages,
      {
        role: "user",
        type: "text",
        content: query,
      },
    ];
    console.log("Added user message, new messages:", messages);
    
    if (socket && socket.readyState === WebSocket.OPEN) {
      loading = true;
      webSocketStore.send(
        JSON.stringify({ type: "chat_message", action: "send", content: query, session_id })
      );
      streaming_response = false;
    }
  };

  const clearMessages = async () => {
    try {
      await deleteChat(session_id)
      messages = []
      window.location.reload()
    } catch (error) {
      console.error("Error deleting chat:", error);
    }
  };

  $: if (session_id) {
    loadSession(session_id)
    if (socket && socket.readyState === WebSocket.OPEN) {
      if (unsubscribe) unsubscribe()
    }
    startWebSocketConnection()
  }

  $: if (messages.length) {
    if (autoscroll) {
      container.scrollTo(0, container.scrollHeight);
    }
  }

  // Add this reactive statement to log messages changes
$: {
  console.log("Messages changed:", messages);
}

  // onDestroy(() => {
  //   if (unsubscribe) unsubscribe()
  //   if (audioContext) {
  //     audioContext.close();
  //   }
  // })
</script>

<div class="main-chat-container">
  <div class="chat-content" bind:this={container}>
    {#each messages as message, index (index)}
      <MessageComponent
        {...message}
        thought={message.thought}
        observation={message.observation}
        reflection={message.reflection}
      />
    {/each}
    {#if loading && !messages.length}
      <Loader />
    {/if}
  </div>
  {#if showClearButton && messages.length}
    <button class="clear-btn" on:click={clearMessages}>
      <i class="fa-solid fa-comment-slash fa-fw"></i>Clear
    </button>
  {/if}
  <Inputbar
    onSubmit={sendMessage}
    {onFileSelect}
    {loading}
    {inputPlaceholder}
    on:audioChunk={handleAudioChunk}
    on:audioComplete={handleAudioComplete}
  />
</div>

<style>
  .main-chat-container {
    display: flex;
    flex: 1;
    flex-direction: column;
    height: 100%;
    min-width: 0;
    position: relative;
  }

  .chat-content {
    container-type: size;
    display: flex;
    flex-direction: column;
    -webkit-box-flex: 1;
    flex-grow: 1;
    position: relative;
    overflow: hidden auto;
    margin: 20px;
    gap: 20px;
    scrollbar-width: thin;
  }

  .clear-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    position: absolute;
    inset-block-end: 60px;
    inset-inline-start: 50%;
    transform: translateX(-50%);
    padding: 5px 10px;
    border-radius: 25px;
    border: 1px solid var(--fg-2);
    color: var(--bg-1);
    background-color: var(--fg-2);
    font-size: 0.8rem;
    cursor: pointer;
    width: fit-content;
    margin: 0 auto;
  }

  .clear-btn:hover {
    background-color: var(--fg-1);
  }
</style>
