<script lang="ts">
  import { onMount } from 'svelte';
  import { fade, fly } from 'svelte/transition';
  import Inputbar from '../lib/Inputbar.svelte';
  import Modal from '../lib/Modal.svelte';
  import type { TeamInterface, TaskDetailInterface, MessageInterface } from '../common/interfaces';
  import { convertXmlToJson, getTeam, saveTask } from '../common/utils';
  import { webSocketStore } from '../common/stores';
    import CodeBlock from '$lib/CodeBlock.svelte';
    import { marked } from 'marked';
    import Message from '$lib/Message.svelte';

  export let team_id: string = "";
  
  let team: TeamInterface | undefined;
  let messages: MessageInterface[] = [];
  let taskStarted = false;
  let task: TaskDetailInterface | null = null;
  let chatContainer: HTMLElement;
  let showTaskModal = false;
  let generatedTask: TaskDetailInterface | null = null;
  let generatedTaskDetails: string  = '';
  let generatingTask = false;
  let streaming_response: boolean = false;

  const loadTeam = async (team_id: string) => {
    try {
      team = (await getTeam(team_id)) as TeamInterface;
    } catch (error) {
      console.log(error);
    }
  };

  function handleSendMessage(message: string) {
    if (!taskStarted) {
      startTask(message);
    } else {
      addMessage({ role: 'user', content: message });
      webSocketStore.send(JSON.stringify({
        type: 'chat_message',
        content: message
      }));
    }
  }

  function startTask(description: string) {
    if (team) {
      generatingTask = true;
      webSocketStore.send(JSON.stringify({
        type: 'generate',
        action: 'task_details',
        prompt: description
      }));
      addMessage({ role: 'system', content: `Generating task details for: ${description}` });
    }
  }

  function handleStartTask() {
    if (generatedTask) {
      taskStarted = true;
      showTaskModal = false;
      addMessage({ role: 'system', content: `Task "${generatedTask.title}" started. You can now interact with the agents.` });
      handleTaskSubmit();
    }
  }

  function addMessage(message: MessageInterface) {
    messages = [...messages, message];
    setTimeout(() => {
      if (chatContainer) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
      }
    }, 0);
  }

  onMount(() => {
    if (!team_id) {
      console.error("Team data is not available");
    }

    const unsubscribe = webSocketStore.subscribe((event) => {
      if (event && event.type === 'message') {
        const data = JSON.parse(event.data);
        
        if (data.type === 'team_message') {
          console.log(data);
          // addMessage({
          //   role: data.sender,
          //   content: `To ${data.receiver}: ${data.content}`
          // });
          const new_message = {
            role: data.sender,
            content: data.content,
          };

          if (messages[messages.length - 1].role !== new_message.role) {
            addMessage(new_message);
          }
          else {
            messages[messages.length - 1].content = new_message.content;
          }
        } else if (data.type === 'generate' && data.action === 'task_details') {
          generatingTask = false;
          generatedTaskDetails = generatedTaskDetails + data.content;
        }
      }
    });

    return () => {
      unsubscribe();
    };
  });

  const handleTaskSubmit = async () => {
    try {
      // generatedTask = (await saveTask(generatedTask as TaskDetailInterface)) as TaskDetailInterface;
      webSocketStore.send(JSON.stringify({
        type: 'start_task',
        team_id: team_id,
        task: generatedTask
      }));
    } catch (error) {
      console.log(error);
    }
  };

  function handleFileUpload() {
    // Implement file upload logic here
    console.log('File upload not implemented');
  }

  function handleClearMessages() {
    messages = [];
    taskStarted = false;
    generatedTaskDetails = '';
    task = null;
  }

  function toggleTaskModal() {
    showTaskModal = !showTaskModal;
  }

  $: if (generatedTaskDetails) {
    let parsedTask = convertXmlToJson(generatedTaskDetails);

    console.log(parsedTask);

    if (parsedTask.description && typeof parsedTask.description === 'object') {
      let description = parsedTask.description['#text'];
      description += '\n\n<steps>' + parsedTask.description.steps + '</steps>';
      parsedTask.description = description;
    }

    generatedTask = {
      title: parsedTask.title,
      description: parsedTask.description,
      assigned_agents: team?.assigned_agents || [],
      status: "pending",
      autostart: true,
      done: false
    };

    generatingTask = false;
    showTaskModal = true;
  }

  $: if (team_id) {
    loadTeam(team_id);
  }
</script>

<Modal
  isOpen={showTaskModal}
  onClose={() => (showTaskModal = false)}
  title={generatedTask?.title || "Generated Task"}
  size="large"
  action={handleStartTask}
  actionLabel="Start Task"
>
  <div class="task-details">
    {#if generatedTask?.description}
      {@html marked(generatedTask?.description)}
    {/if}
  </div>
</Modal>

<div class="team-interaction" class:task-started={taskStarted}>
  {#if team}
    <header class:centered={!taskStarted}>
      <h1>{team.name}</h1>
      {#if !taskStarted}
        <p>Start a new task by typing a description below.</p>
        {#if generatingTask}
          <div class="generating-task">
            <p>Generating task details...</p>
            <div class="spinner"></div>
          </div>
        {/if}
        <div class="centered-input">
          <Inputbar
            sendMessage={handleSendMessage}
            uploadFile={handleFileUpload}
            clearMessages={handleClearMessages}
            placeholder="Describe the task to start..."
          />
        </div>
      {/if}
    </header>

    {#if taskStarted}
      <div class="chat-header">
        <h2>Task: {generatedTask?.title}</h2>
        <button class="btn secondary" on:click={toggleTaskModal}>
          <i class="fas fa-info-circle"></i> View Task Details
        </button>
      </div>
      <div class="chat-area" bind:this={chatContainer}>
        {#each messages as message, i (i)}
          <Message {...message} />
          <div
            class="message {message.role}"
            in:fly={{ y: 20, duration: 300 }}
            out:fade={{ duration: 200 }}
          >
            <div class="message-content">
              <strong>{message.role}:</strong>
              {message.content}
            </div>
          </div>
        {/each}
      </div>
      <Inputbar
        sendMessage={handleSendMessage}
        uploadFile={handleFileUpload}
        clearMessages={handleClearMessages}
        placeholder="Type your message..."
      />
    {/if}
  {:else}
    <div class="error">Team data is not available</div>
  {/if}
</div>

<style>
  .team-interaction {
    display: flex;
    flex-direction: column;
    height: 100vh;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    box-sizing: border-box;
    background-color: var(--bg-0);
  }

  header {
    text-align: center;
    margin-bottom: 20px;
  }

  header.centered {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    height: 100%;
  }

  h1 {
    font-size: 2em;
    margin-bottom: 10px;
  }

  .centered-input {
    width: 100%;
    max-width: 600px;
    margin-top: 20px;
  }

  .chat-area {
    flex-grow: 1;
    overflow-y: auto;
    margin-bottom: 20px;
    padding: 10px;
    border-radius: 8px;
    background-color: var(--bg-1);
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  }

  .message {
    margin-bottom: 15px;
    padding: 10px;
    border-radius: 8px;
    max-width: 80%;
  }

  .message.user {
    /* background-color: var(--primary-color); */
    color: white;
    align-self: flex-end;
    margin-left: auto;
  }

  .message.assistant, .message.system {
    /* background-color: var(--fg-1); */
    align-self: flex-start;
  }

  .message-content {
    word-wrap: break-word;
    color: var(--fg-1);
  }

  .task-started :global(.input-bar) {
    position: sticky;
    bottom: 0;
    background-color: var(--bg-0);
    padding: 10px 0;
  }

  .error {
    color: var(--error-color);
    text-align: center;
    font-size: 1.2em;
    margin-top: 20px;
  }

  .generating-task {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 20px;
    background-color: var(--bg-1);
    border-radius: 8px;
    margin-bottom: 20px;
  }

  .spinner {
    border: 4px solid var(--bg-2);
    border-top: 4px solid var(--accent-color);
    border-radius: 50%;
    width: 40px;
    height: 40px;
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }

  .task-details {
    display: flex;
    flex-direction: column;
    gap: 15px;
    padding: 10px;
    text-align: start;
  }

  :global(.task-details ul), :global(.task-details ol) {
    padding: 0 15px;
  }

  .btn.primary {
    background-color: var(--accent-color);
    color: white;
    border: none;
    padding: 10px 15px;
    border-radius: 5px;
    cursor: pointer;
    font-weight: bold;
    transition: background-color 0.3s ease;
  }

  .btn.primary:hover {
    background-color: var(--accent-color-dark);
  }

  .chat-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
    padding: 10px;
    background-color: var(--bg-1);
    border-radius: 8px;
  }

  .chat-header h2 {
    margin: 0;
    font-size: 1.2em;
    color: var(--fg-1);
  }

  .btn.secondary {
    background-color: var(--bg-2);
    color: var(--fg-1);
    border: none;
    padding: 8px 12px;
    border-radius: 5px;
    cursor: pointer;
    font-size: 0.9em;
    transition: background-color 0.3s ease;
    display: flex;
    align-items: center;
    gap: 5px;
  }

  .btn.secondary:hover {
    background-color: var(--bg-3);
  }

  .btn.secondary i {
    font-size: 1em;
  }
</style>
