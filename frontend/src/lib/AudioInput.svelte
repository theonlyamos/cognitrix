<script lang="ts">
  import { createEventDispatcher } from "svelte";

  const dispatch = createEventDispatcher();
  let recording = $state(false);
  let mediaRecorder: MediaRecorder | null = null;
  let audioChunks: Blob[] = [];

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);

      mediaRecorder.ondataavailable = (event) => {
        audioChunks.push(event.data);
        dispatch("audioChunk", { data: event.data });
      };

      mediaRecorder.onstop = () => {
        const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
        dispatch("audioComplete", { blob: audioBlob });
        audioChunks = [];
      };

      mediaRecorder.start(1000);
      recording = true;
    } catch (error) {
      console.error("Error accessing microphone:", error);
    }
  }

  function stopRecording() {
    if (mediaRecorder && recording) {
      mediaRecorder.stop();
      mediaRecorder.stream.getTracks().forEach((track) => track.stop());
      recording = false;
    }
  }
</script>

<button
  class="audio-btn"
  on:mousedown={startRecording}
  on:mouseup={stopRecording}
  on:mouseleave={stopRecording}
>
  <i class="fas fa-microphone" class:recording></i>
</button>

<style>
  .audio-btn {
    background: transparent;
    border: none;
    cursor: pointer;
    padding: 8px;
    border-radius: 50%;
    transition: all 0.2s ease;
  }

  .audio-btn:hover {
    background: var(--bg-2);
  }

  .fa-microphone {
    color: var(--fg-1);
    font-size: 1.2em;
  }

  .recording {
    color: #ef4444;
    animation: pulse 1.5s infinite;
  }

  @keyframes pulse {
    0% {
      transform: scale(1);
    }
    50% {
      transform: scale(1.2);
    }
    100% {
      transform: scale(1);
    }
  }
</style>
