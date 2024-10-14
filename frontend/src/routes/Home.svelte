<script lang="ts">
  import { onMount } from "svelte";
  import { link, navigate } from "svelte-routing";
  import type { SessionInterface } from "../common/interfaces";
  import { createSession, deleteSession, getAllSessions } from "../common/utils";
  import ChatComponent from "../lib/ChatContent.svelte";

  export let session_id: string = "";
  export let agent_id: string = "";

  let sessions: SessionInterface[] = [];
  let loading: boolean = true;


  const loadAllSessions = async () => {
    loading = true
    try {
      const response = await getAllSessions() as SessionInterface[]
      sessions = response
    } catch (error) {
      console.error("Error loading sessions:", error);
    } finally {
      loading = false
    }
  }

  const removeSession = async (event: MouseEvent, sessionId: string) => {
    // event.stopPropagation();
    event.preventDefault();
    try {
      await deleteSession(sessionId)
      navigate('/')
    } catch (error) {
      console.error("Error deleting session:", error);
    }
  };

  onMount(() => {
    loadAllSessions()
  });

  async function createNewSession() {
    let sessionData: any = {}
    if (agent_id) {
      sessionData.agent_id = agent_id;
    }
    try {
      const newSession = await createSession(sessionData);
      navigate(`/${newSession.id}`);
      window.location.reload()
    } catch (error) {
      console.error("Error creating new session:", error);
    }
  }
</script>

<div class="container">
  <div class="chat-sessions">
    <div class="session-header">
      <h3>Sessions</h3>
      <button on:click={createNewSession} title="Create new session">
        <i class="fa-solid fa-plus"></i>
      </button>
    </div>
    {#each sessions as session (session.id)}
      <a href="/{session.id}" use:link class="session-item">
        <span>{new Date(session.created_at).toLocaleString()}</span>
        {#if session.id !== session_id}
          <button on:click={(e) => removeSession(e, session.id)}>
            <i class="fa-solid fa-xmark"></i>
          </button>
        {/if}
      </a>
    {/each}
  </div>

  {#if session_id}
    <ChatComponent {session_id} />
  {/if}
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

  .session-header {
    padding: 10px;
    border-block-end: 1px solid var(--bg-1);
    white-space: nowrap;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
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
