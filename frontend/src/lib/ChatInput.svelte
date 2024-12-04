<script lang="ts">
  import { createEventDispatcher } from "svelte";
  import AudioInput from "./AudioInput.svelte";

  const dispatch = createEventDispatcher();

  interface Props {
    loading?: boolean;
    inputPlaceholder?: string;
    onFileSelect?: (file: File) => void;
  }

  let {
    loading = false,
    inputPlaceholder = "Type a message...",
    onFileSelect = () => {},
  }: Props = $props();

  let message = $state("");
  let fileInput: HTMLInputElement;

  function handleSubmit() {
    if (message.trim()) {
      dispatch("submit", { message });
      message = "";
    }
  }

  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSubmit();
    }
  }

  function handleFileChange(event: Event) {
    const target = event.target as HTMLInputElement;
    if (target.files?.length) {
      onFileSelect(target.files[0]);
    }
  }
</script>

<div class="chat-input">
  <div class="input-container">
    <textarea
      bind:value={message}
      on:keydown={handleKeyDown}
      placeholder={inputPlaceholder}
      rows="1"
      disabled={loading}
    />
    <div class="actions">
      <button
        class="file-btn"
        on:click={() => fileInput.click()}
        disabled={loading}
      >
        <i class="fas fa-paperclip"></i>
      </button>
      <AudioInput on:audioChunk on:audioComplete disabled={loading} />
      <button
        class="send-btn"
        on:click={handleSubmit}
        disabled={loading || !message.trim()}
      >
        <i class="fas fa-paper-plane"></i>
      </button>
    </div>
  </div>
  <input
    type="file"
    bind:this={fileInput}
    on:change={handleFileChange}
    style="display: none"
  />
</div>

<style>
  .chat-input {
    padding: 16px;
    background: var(--bg-1);
    border-top: 1px solid var(--border-color);
  }

  .input-container {
    display: flex;
    gap: 12px;
    align-items: flex-end;
    background: var(--bg-2);
    border-radius: 12px;
    padding: 8px 16px;
  }

  textarea {
    flex: 1;
    border: none;
    background: transparent;
    resize: none;
    padding: 8px 0;
    max-height: 150px;
    font-family: inherit;
    font-size: 1em;
    color: var(--fg-1);
  }

  textarea:focus {
    outline: none;
  }

  .actions {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .file-btn,
  .send-btn {
    background: transparent;
    border: none;
    cursor: pointer;
    padding: 8px;
    border-radius: 50%;
    transition: all 0.2s ease;
  }

  .file-btn:hover,
  .send-btn:hover {
    background: var(--bg-1);
  }

  .file-btn i,
  .send-btn i {
    color: var(--fg-1);
    font-size: 1.2em;
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
