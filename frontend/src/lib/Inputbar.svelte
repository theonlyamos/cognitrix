<script lang="ts">
  import type { MouseEventHandler } from "svelte/elements";

  export let uploadFile: Function;
  export let sendMessage: Function;
  export let loading: boolean = false;
  export let clearMessages: MouseEventHandler<HTMLElement> = () => {};
  export let placeholder: string = "Enter your message...";
  export let clearButton: boolean = false;

  let inputElement: HTMLDivElement;
  let userInput = "";

  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (userInput.trim()) onSendMessage();
    }
  }

  const onSendMessage = async () => {
    if (userInput.trim()) {
      await sendMessage(userInput.trim());
      userInput = "";
      inputElement.innerText = "";
    }
  };
</script>

<div class="input-bar">
  <div class="input-container">
    <div
      role="textbox"
      tabindex="0"
      class="input-box"
      contenteditable="true"
      on:keydown={handleKeyDown}
      bind:innerHTML={userInput}
      bind:this={inputElement}
      {placeholder}
    ></div>
    <button on:click={(event) => uploadFile(event)}>
      <i class="fas fa-paperclip"></i>
    </button>
    <button
      on:click={onSendMessage}
      disabled={!userInput.trim() || loading}
      class={`${userInput.trim() && !loading ? "" : "disabled"}`}
    >
      {#if loading}
        <i class="fa-solid fa-circle-stop"></i>
      {:else}
        <i class="fa-solid fa-paper-plane"></i>
      {/if}
    </button>
  </div>

  {#if clearButton}
    <button class="clear-btn" on:click={clearMessages}>
      <i class="fa-solid fa-comment-slash fa-fw"></i>Clear
    </button>
  {/if}
</div>

<style>
  .input-bar {
    align-items: end;
    background: none;
    display: flex;
    padding: 12px 24px 6px 24px;
    position: relative;
  }

  .input-container {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-radius: 20px;
    font-size: 0.9rem;
    background-color: var(--fg-1);
    padding: 5px 20px;
  }

  .input-box {
    text-align: start;
    outline: none;
    padding: 10px;
    width: 90%;
    max-height: 100px;
    overflow-y: auto;
    color: var(--bg-1) !important;
  }

  .input-box:empty::before {
    content: attr(placeholder);
    color: var(--bg-2);
    opacity: 0.6;
  }

  .input-container button {
    background-color: var(--fg-1);
    color: var(--bg-1);
    padding: 0;
    border: 0;
    outline: none;
    opacity: 0.7;
  }

  .input-container button:hover,
  .input-container button:focus {
    border: none;
    padding: 0 !important;
    outline: none;
    opacity: 1;
  }

  .clear-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    position: absolute;
    top: -20px;
    left: 50%;
    transform: translateX(-50%);
    padding: 5px 10px;
    border-radius: 25px;
    border: 1px solid var(--fg-2);
    color: var(--bg-1);
    background-color: var(--fg-2);
    font-size: 0.8rem;
    cursor: pointer;
    width: fit-content;
  }

  .clear-btn:hover {
    background-color: var(--fg-1);
  }
</style>
