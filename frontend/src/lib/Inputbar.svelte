<!-- @migration-task Error while migrating Svelte code: Can't migrate code with afterUpdate and beforeUpdate. Please migrate by hand. -->
<script lang="ts">
  import { afterUpdate, beforeUpdate, createEventDispatcher } from 'svelte';
  import { onDestroy } from 'svelte';
  import { liveTranscription } from '../common/deepgram';

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
  let transcriptionController: { start: () => void; stop: () => void, onTranscript: (callback: (data: string) => void) => void } | null = null;


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
        console.error('Error starting live transcription:', error);
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
    <button on:click={(event) => onFileSelect(event)}>
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
    <div class="btn-group">
      <button on:click={toggleRecording}>
        <i class="fa-solid fa-microphone" class:recording={isTranscribing}></i>
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
  </div>
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
    scrollbar-width: thin;
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

  .btn-group {
    display: flex;
    gap: 10px;
  }

  .fa-microphone.recording {
    color: #ff4136; /* Red color when recording */
  }
</style>
