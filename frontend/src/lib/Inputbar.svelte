<!-- @migration-task Error while migrating Svelte code: Can't migrate code with afterUpdate and beforeUpdate. Please migrate by hand. -->
<script lang="ts">
  import { afterUpdate, beforeUpdate, createEventDispatcher } from "svelte";
  import { onDestroy } from "svelte";
  import { liveTranscription } from "../common/deepgram";

  export let onFileSelect: Function;
  export let onSubmit: Function;
  export let loading: boolean = false;
  export let inputPlaceholder: string = "Enter your message...";

  let inputElement: HTMLElement;
  let userInput = "";
  let isRecording = false;
  let mediaRecorder: MediaRecorder | null = null;
  let audioChunks: Blob[] = [];
  let autoscroll = false;

  const dispatch = createEventDispatcher();

  let isTranscribing = false;
  let transcriptionController: {
    start: () => void;
    stop: () => void;
    onTranscript: (callback: (data: string) => void) => void;
  } | null = null;

  beforeUpdate(() => {
    if (inputElement) {
      const scrollableDistance =
        inputElement.scrollHeight - inputElement.offsetHeight;
      autoscroll = inputElement.scrollTop > scrollableDistance - 20;
    }
  });

  afterUpdate(() => {
    if (autoscroll) {
      inputElement.scrollTo(0, inputElement.scrollHeight);
    }
  });

  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (userInput.trim()) onSendMessage();
    }
  }

  const onSendMessage = async () => {
    if (userInput.trim()) {
      await onSubmit(userInput.trim());
      userInput = "";
      inputElement.innerText = "";
    }
  };

  async function toggleRecording() {
    if (!isTranscribing) {
      try {
        if (!transcriptionController) {
          transcriptionController = await liveTranscription();
        }
        transcriptionController.onTranscript((data: string) => {
          console.log("Transcript:", data);
          userInput = userInput + data + " ";
        });
        transcriptionController.start();
        isTranscribing = true;
      } catch (error) {
        console.error("Error starting live transcription:", error);
      }
    } else {
      if (transcriptionController) {
        transcriptionController.stop();
        isTranscribing = false;
      }
    }
  }

  onDestroy(() => {
    if (isTranscribing && transcriptionController) {
      transcriptionController.stop();
    }
  });
</script>

<div class="input-bar">
  <div class="input-container">
    <button
      class="action-btn"
      on:click={(event) => onFileSelect(event)}
      title="Attach file"
    >
      <i class="fa-solid fa-paperclip"></i>
    </button>

    <div
      role="textbox"
      tabindex="0"
      class="input-box"
      contenteditable="true"
      on:keydown={handleKeyDown}
      bind:innerHTML={userInput}
      bind:this={inputElement}
      placeholder={inputPlaceholder}
    ></div>

    <div class="action-buttons">
      <button
        class="action-btn"
        on:click={toggleRecording}
        title={isTranscribing ? "Stop recording" : "Start recording"}
      >
        <i class="fa-solid fa-microphone" class:recording={isTranscribing}></i>
      </button>

      <button
        class="action-btn send-btn"
        on:click={onSendMessage}
        disabled={!userInput.trim() || loading}
        title={loading ? "Stop" : "Send message"}
      >
        {#if loading}
          <i class="fa-solid fa-circle-stop"></i>
        {:else}
          <i class="fa-solid fa-paper-plane"></i>
        {/if}
      </button>
    </div>
  </div>
</div>

<style>
  .input-bar {
    position: sticky;
    bottom: 0;
    padding: 1rem 1.5rem;
    background: var(--bg-0);
    border-top: 1px solid var(--bg-2);
  }

  .input-container {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    background: var(--bg-1);
    border-radius: 1rem;
    padding: 0.75rem 1rem;
    box-shadow: 0 2px 4px rgb(0 0 0 / 0.1);
  }

  .input-box {
    flex: 1;
    min-height: 1.5rem;
    max-height: 150px;
    overflow-y: auto;
    padding: 0.25rem;
    color: var(--fg-1);
    font-size: 0.9375rem;
    line-height: 1.5;
    scrollbar-width: thin;
  }

  .input-box:empty::before {
    content: attr(placeholder);
    color: var(--fg-2);
    opacity: 0.7;
  }

  .action-buttons {
    display: flex;
    gap: 0.5rem;
  }

  .action-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border-radius: 0.5rem;
    border: none;
    background: transparent;
    color: var(--fg-2);
    transition: all 150ms ease;
    cursor: pointer;
  }

  .action-btn:hover {
    background: var(--bg-2);
    color: var(--fg-1);
  }

  .action-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .send-btn {
    background: var(--accent-color);
    color: var(--bg-0);
  }

  .send-btn:hover:not(:disabled) {
    background: var(--accent-color-dark);
    color: var(--bg-0);
  }

  .fa-microphone.recording {
    color: #ef4444;
  }
</style>
