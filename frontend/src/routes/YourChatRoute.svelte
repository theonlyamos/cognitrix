<script lang="ts">
  import ChatContent from "../lib/ChatContent.svelte";
  import type { MessageInterface } from "../common/interfaces";

  let messages: MessageInterface[] = $state([]);
  let loading = $state(false);

  function handleSubmit(message: string) {
    messages = [
      ...messages,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: message,
        type: "text",
        timestamp: new Date().toISOString(),
      },
    ];
    // Handle sending message to backend...
  }

  function handleFileSelect(file: File) {
    // Handle file upload...
  }
</script>

<ChatContent
  {messages}
  {loading}
  onSubmit={handleSubmit}
  onFileSelect={handleFileSelect}
  inputPlaceholder="Type your message..."
/>
