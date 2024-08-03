<script lang="ts">
  import type { MouseEventHandler } from "svelte/elements";

  export let uploadFile: Function;
  export let sendMessage: Function;
  export let loading: boolean = false;
  export let clearMessages: MouseEventHandler<HTMLElement> = () => {};

  let inputElement: any;
  let userInput = "Enter prompt here...";

  const onfocus = (event: FocusEvent) => {
    if (userInput === "Enter prompt here...") userInput = "";
  };

  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (userInput) onSendMessage();
    }
  }

  const onSendMessage = async () => {
    await sendMessage(userInput);
    userInput = "";
  };
</script>

<div class="input-bar">
  <div class="input-container">
    <div
      role="textbox"
      tabindex="0"
      class="input-box"
      contenteditable="true"
      on:focus={onfocus}
      on:keydown={handleKeyDown}
      bind:innerText={userInput}
      bind:this={inputElement}
    >
      {userInput}
    </div>
    <button
      on:click={() => {
        uploadFile();
      }}
    >
      <i class="fas fa-paperclip"></i>
    </button>
    <button
      on:click={onSendMessage}
      disabled={!userInput || loading}
      class={`${userInput || loading ? "" : "disabled"}`}
    >
      {#if loading}
        <i class="fas fa-circle-stop"></i>
      {:else}
        <i class="fas fa-paper-plane"></i>
      {/if}
    </button>
  </div>

  <button class="clear-btn" on:click={clearMessages}>
    <i class="fa-solid fa-comment-slash fa-fw"></i>Clear
  </button>
</div>

<style>
  .input-bar {
    -webkit-box-align: end;
    align-items: end;
    background: none;
    display: flex;
    padding: 12px 24px 6px 24px;
    position: relative;
  }

  .input-container {
    inline-size: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-radius: 20px;
    font-size: 0.9rem;
    background-color: var(--bg-1);
    color: var(--fg-2);
    padding: 5px 20px;
    /* max-block-size: 100px; */
  }

  .input-box {
    text-align: start;
    outline: none;
    padding: 10px;
    inline-size: 90%;
    max-block-size: 100px;
    overflow-y: auto;
    color: var(--fg-1) !important;

    &::-webkit-scrollbar {
      display: none;
    }
  }

  .input-container button {
    background-color: var(--bg-1);
    padding: 0;
    border: 0;
    outline: none;

    &:hover,
    &:focus {
      border: none;
      padding: 0 !important;
      outline: none;
    }
  }

  .input-container i {
    color: var(--fg-2);
  }

  .clear-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    position: absolute;
    inset-block-start: -20px;
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

    &:hover {
      background-color: var(--fg-1);
    }
  }
</style>
