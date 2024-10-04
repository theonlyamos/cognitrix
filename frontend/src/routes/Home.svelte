<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { link, navigate, useLocation } from "svelte-routing";
  import type {
    MessageInterface,
    SessionInterface,
  } from "../common/interfaces";
  import ChatComponent from "../lib/ChatContent.svelte";
  import InputBar from "../lib/Inputbar.svelte";
  import { webSocketStore } from "../common/stores";
  import type { Unsubscriber } from "svelte/motion";

  export let session_id: string = "";
  export let agent_id: string = "";

  let sessions: SessionInterface[] = [];
  let messages: MessageInterface[] = [];
  let agentName: string = "Assistant";
  let loading: boolean = true;
  let loadingMessages: boolean = true;
  let unsubscribe: Unsubscriber | null = null;
  let socket: WebSocket;
  let streaming_response: boolean = false;

  const location = useLocation();

  const uploadFile = () => {
    console.log("Uploading file...");
  };

  const sendMessage = async (query: string) => {
    loading = true;
    messages = [
      ...messages,
      {
        role: "user",
        content: query,
      },
    ];

    if (socket && socket.readyState === WebSocket.OPEN) {
      webSocketStore.send(
        JSON.stringify({ type: "chat_message", action: "send", content: query })
      );
      streaming_response = false;
    }
  };

  const deleteSession = async (event: MouseEvent, sessionId: string) => {
    // event.stopPropagation();
    event.preventDefault();
    if (socket && socket.readyState === WebSocket.OPEN) {
      webSocketStore.send(
        JSON.stringify({
          type: "sessions",
          action: "delete",
          session_id: sessionId,
        })
      );
    }
  };

  const clearMessages = async () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      webSocketStore.send(
        JSON.stringify({
          type: "chat_history",
          action: "delete",
          session_id: session_id,
        })
      );
    }
  };

  const resetState = () => {
    messages = [];
    agentName = "Assistant";
    loading = true;
    sessions = [];
  };

  const handleRouteChange = () => {
    resetState();
    if (socket && socket.readyState === WebSocket.OPEN) {
      webSocketStore.send(JSON.stringify({ type: "sessions", action: "list" }));

      if (agent_id) {
        webSocketStore.send(
          JSON.stringify({
            type: "sessions",
            action: "get",
            agent_id: agent_id,
          })
        );
      } else if (session_id) {
        loadingMessages = true;
        webSocketStore.send(
          JSON.stringify({
            type: "chat_history",
            action: "get",
            session_id: session_id,
          })
        );
      }
    }
  };

  const startWebSocketConnection = () => {
    unsubscribe = webSocketStore.subscribe(
      (event: { socket: WebSocket; type: string; data?: any }) => {
        if (event !== null) {
          socket = event.socket;
          if (event.type === "open") {
            if (socket && socket.readyState === WebSocket.OPEN) {
              webSocketStore.send(
                JSON.stringify({ type: "sessions", action: "list" })
              );

              handleRouteChange();
            }
          } else if (event.type === "message") {
            loading = false;
            loadingMessages = false;
            let data = JSON.parse(event.data);

            if (data.type === "chat_history") {
              agentName = data.agent_name;
              if (data.action == "delete") {
                messages = [];
                window.location.reload();
              } else {
                console.log(data.content);
                for (let msg of data.content) {
                  messages = [
                    ...messages,
                    {
                      role: msg.role.toLowerCase(),
                      content: msg.message,
                      artifacts: msg.artifacts,
                    },
                  ];
                }
              }
            } else if (data.type === "chat_message") {
              const new_message = {
                role: agentName,
                content: data.content,
              };

              if (!streaming_response) {
                messages = [...messages, new_message];
              } else {
                if (data.complete) {
                  messages[messages.length - 1].content = new_message.content;
                } else {
                  messages[messages.length - 1].content =
                    messages[messages.length - 1].content + new_message.content;
                }
              }

              streaming_response = true;
            } else if (data.type === "sessions") {
              if (data.action === "list" || data.action === "delete") {
                sessions = data.content as SessionInterface[];
              } else if (data.action === "get") {
                session_id = data.content?.id;
              }
            }
          }
        }
      }
    );
  };

  onMount(() => {
    startWebSocketConnection();
    handleRouteChange();
  });

  onDestroy(() => {
    if (unsubscribe) unsubscribe();
  });

  $: {
    // React to changes in the route
    const currentPath = $location.pathname;

    if (currentPath === "/" || session_id || agent_id) {
      if (currentPath === "/") session_id = "";
      handleRouteChange();
    }
  }
</script>

<div class="container">
  <div class="chat-sessions">
    <h3>Chat Sessions</h3>
    {#each sessions as session (session.id)}
      <a href="/{session.id}" use:link class="session-item">
        <span>{session.datetime}</span>
        <button on:click={(e) => deleteSession(e, session.id)}>
          <i class="fa-solid fa-xmark"></i>
        </button>
      </a>
    {/each}
  </div>

  <ChatComponent {messages} loading={loadingMessages}>
    {#if session_id}
      <InputBar
        {uploadFile}
        {sendMessage}
        {loading}
        {clearMessages}
        clearButton={true}
      />
    {/if}
  </ChatComponent>
</div>

<style>
  .container {
    inline-size: 100%;
    block-size: 100%;
    display: flex;
    justify-content: space-between;
    gap: 10px;
    position: relative;
    padding-inline: 0;
    padding-block: 0;
  }
  .chat-sessions {
    inline-size: 200px;
    block-size: 100%;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    overflow-y: auto;
    text-align: start;
    border-inline-end: 1px solid var(--bg-1);
    color: var(--fg-1);
  }

  h3 {
    margin-block-end: 0;
    padding-block: 0 10px;
    padding-inline: 10px 10px;
    border-block-end: 1px solid var(--bg-1);
    white-space: nowrap;
  }

  .session-item {
    color: var(--fg-1);
    font-size: 0.8rem;
    padding-inline: 10px 10px;
    padding-block: 10px 0;
    position: relative;

    &:hover,
    &:focus,
    &:active {
      color: var(--fg-2);
    }
  }

  .session-item button {
    position: absolute;
    inset-block-start: 11px;
    inset-inline-end: 2px;
    display: none;
    color: rgb(235, 22, 22);
  }

  .session-item:hover button {
    display: block;
  }
</style>
