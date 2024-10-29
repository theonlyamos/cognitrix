<script lang="ts">
  import { onMount } from 'svelte';
  import { fade, fly } from 'svelte/transition';
  import Inputbar from '../lib/Inputbar.svelte';
  import Modal from '../lib/Modal.svelte';
  import type { TeamInterface, TaskDetailInterface, MessageInterface, SessionInterface } from '../common/interfaces';
  import { convertXmlToJson, getTeam, saveTask, getTask, getTaskSession, getTasksByTeam, getAllTasks, getSessionsByTeam } from '../common/utils';
  import { webSocketStore } from '../common/stores';
  import CodeBlock from '$lib/CodeBlock.svelte';
  import { marked } from 'marked';
  import Message from '$lib/Message.svelte';
    import Accordion from '$lib/Accordion.svelte';
    import Alert from '$lib/Alert.svelte';
    import { Link, navigate } from 'svelte-routing';

  export let team_id: string = "";
  export let task_id: string | null = null;
  export let session_id: string | null = null;
  
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
  let taskSessions: SessionInterface[] = [];
  let selectedSession: SessionInterface | null = null;
  let isLoading = false;
  let error: string | null = null;
  let allTasks: TaskDetailInterface[] = [];
  let selectedTaskId: string | null = null;
  let teamSessions: SessionInterface[] = [];

  const loadTeam = async (team_id: string) => {
    isLoading = true;
    error = null;
    try {
      team = await getTeam(team_id);
      // if (!task_id) {
      allTasks = await getAllTasks() as TaskDetailInterface[];
      // }
    } catch (err) {
      console.error('Failed to load team:', err);
      error = 'Failed to load team data. Please try again.';
    } finally {
      isLoading = false;
    }
  };

  const loadAllTasks = async () => {
    isLoading = true; 
    // error = null;
    try {
      allTasks = await getAllTasks() as TaskDetailInterface[];
    } catch (err) {
      console.error('Failed to load all tasks:', err);
      // error = 'Failed to load all tasks. Please try again.';
    } finally {
      isLoading = false;
    }
  }

  const loadTask = async (task_id: string) => {
    isLoading = true;
    error = null;
    try {
      task = await getTask(task_id) as TaskDetailInterface;
      // taskStarted = true;
      await loadTaskSessions(task_id);
    } catch (err) {
      console.error('Failed to load task:', err);
      error = 'Failed to load task data. Please try again.';
    } finally {
      isLoading = false;
    }
  };

  const loadTaskSessions = async (task_id: string) => {
    try {
      // const tasks = await getS(team_id);
      const sessions = await getSessionsByTeam(team_id);
      taskSessions = sessions.filter(session => session.task_id === task_id) as SessionInterface[];
    } catch (err) {
      console.error('Failed to load task sessions:', err);
      error = 'Failed to load task sessions. Please try again.';
    }
  };

  const loadSessionChat = async (session_id: string) => {
    isLoading = true;
    error = null;
    try {
      const sessionData = await getTaskSession(session_id);
      selectedSession = sessionData;
      messages = sessionData.chat as MessageInterface[];
      const task = allTasks.find(task => task.id === sessionData.task_id) as TaskDetailInterface;
      generatedTask = task;
    } catch (err) {
      console.error('Failed to load session chat:', err);
      error = 'Failed to load chat history. Please try again.';
    } finally {
      isLoading = false;
      taskStarted = true;
    }
  };

  function handleSendMessage(message: string) {
    if (!taskStarted) {
      generateTaskDetails(message);
    } else {
      addMessage({ role: 'user', content: message });
      webSocketStore.send(JSON.stringify({
        type: 'chat_message',
        content: message
      }));
    }
  }

  function generateTaskDetails(description: string) {
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

  async function handleStartTask() {
    taskStarted = true;
    showTaskModal = false;
    handleTaskSubmit();
    if (selectedTaskId) {
      generatedTask = allTasks.find(task => task.id === selectedTaskId) as TaskDetailInterface;
      addMessage({ role: 'system', content: `Task "${generatedTask?.title}" started. You can now interact with the agents.` });
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

  function handleTaskSelection() {
    if (selectedTaskId) {
      loadTask(selectedTaskId);
    }
  }

  const loadTeamSessions = async (team_id: string) => {
    try {
      teamSessions = await getSessionsByTeam(team_id);
    } catch (err) {
      console.error('Failed to load team sessions:', err);
      error = 'Failed to load team sessions. Please try again.';
    }
  };

  onMount(async () => {
    if (team_id) {
      await loadTeam(team_id);
      await loadTeamSessions(team_id);
    }
    if (session_id){
      await loadSessionChat(session_id);
    }
    else if (task_id) {
      await loadTask(task_id);
    }
  });

  onMount(() => {
    const unsubscribe = webSocketStore.subscribe((event) => {
      if (event && event.type === 'message') {
        const data = JSON.parse(event.data);
        
        if (data.type === 'team_message') {
          // console.log(data);
          const newMessage = {
            role: data.sender,
            content: data.content,
          };

          let lastMessage: MessageInterface = messages[messages.length - 1];

          if (lastMessage.role.toLowerCase() === newMessage.role.toLowerCase()) {
            lastMessage.content += newMessage.content
            messages = [...messages.slice(0, -1), lastMessage];
          }
          else {
            addMessage(newMessage);
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
      webSocketStore.send(JSON.stringify({
        type: 'start_task',
        team_id: team_id,
        task: selectedTaskId ? "" : generatedTask ,
        task_id: selectedTaskId
      }));
    } catch (err) {
      console.error('Failed to submit task:', err);
      error = 'Failed to start the task. Please try again.';
    }
  };

  function handleFileUpload() {
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

  $: if (task_id && !selectedTaskId && !session_id) {
    selectedTaskId = task_id;
  }

  $: if (selectedTaskId) {
    navigate(`/teams/${team_id}/tasks/${selectedTaskId}/interact`);
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
  {#if isLoading}
    <div class="generating-task">
      <p>Loading...</p>
      <div class="spinner"></div>
    </div>
  {:else if error}
    <Alert type="danger" message={error} autoClose={true} />
  {:else if team}
    <div class="interaction-layout">
      <div class="sessions-sidebar">
        {#if teamSessions.length > 0}
          <Accordion title="Team Sessions" opened={true}>
            {#each teamSessions as session}
              <Link
                to={`/teams/${team_id}/tasks/${session?.task_id}/sessions/${session.id}/interact`}
                class="btn session-item"
              >
                {new Date(session?.created_at).toLocaleString()}
              </Link>
            {/each}
          </Accordion>
        {/if}
      </div>
      <div class="main-content">
        <header class:centered={!taskStarted}>
          <i class="fa-solid fa-users fa-3x fa-fw"></i>
          <h1>{team.name}</h1>
          {#if !taskStarted}
            <div class="task-selection">
              <label for="task-dropdown">Select a task</label>
              <select bind:value={selectedTaskId} class="task-dropdown">
                <option value="" selected disabled>Select a task</option>
                {#each allTasks as task}
                  <option value={task.id}>{task.title}</option>
                {/each}
              </select>
              <button
                class="btn primary"
                on:click={handleStartTask}
                disabled={!selectedTaskId}
              >
                Start Selected Task
              </button>
            </div>
            <p>Or start a new task by typing a description below.</p>
            {#if generatingTask}
              <div class="generating-task">
                <p>Generating task details...</p>
                <div class="spinner"></div>
              </div>
            {/if}
            <div class="centered-input">
              <Inputbar
                onSubmit={handleSendMessage}
                onFileSelect={handleFileUpload}
                inputPlaceholder="Describe the task to start..."
              />
            </div>
          {/if}
        </header>

        {#if taskStarted}
          <div class="chat-container">
            <div class="chat-header">
              <h2>Task: {task?.title || generatedTask?.title}</h2>
              <button class="btn secondary" on:click={toggleTaskModal}>
                <i class="fas fa-info-circle"></i> View Task Details
              </button>
            </div>
            <div class="chat-area" bind:this={chatContainer}>
              {#each messages as message}
                <Message {...message} />
              {/each}
            </div>
            <Inputbar
              onSubmit={handleSendMessage}
              onFileSelect={handleFileUpload}
              inputPlaceholder="Type your message..."
            />
          </div>
        {/if}
      </div>
    </div>
  {:else}
    <div class="error">Team data is not available</div>
  {/if}
</div>

<style>
  .team-interaction {
    display: flex;
    flex-direction: column;
    height: 100vh;
    inline-size: 100%;
    margin: 0 auto;
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
    display: flex;
    flex-direction: column;
    flex-grow: 1;
    overflow-y: auto;
    margin-bottom: 20px;
    padding: 10px;
    border-radius: 8px;
    gap: 40px;
    background-color: var(--bg-2);
  }
/* 
  .message {
    margin-bottom: 15px;
    padding: 10px;
    border-radius: 8px;
    max-width: 80%;
  }

  .message.user {
    /* background-color: var(--primary-color);
    color: white;
    align-self: flex-end;
    margin-left: auto;
  }

  .message.assistant, .message.system {
    /* background-color: var(--fg-1);
    align-self: flex-start;
  }

  .message-content {
    word-wrap: break-word;
    color: var(--fg-1);
  } */

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
    transition: background-color 0.3s ease, opacity 0.3s ease;
  }

  .btn.primary:hover {
    background-color: var(--accent-color-dark);
  }

  .btn.primary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
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

  .sessions-sidebar {
    inline-size: 250px;
    padding: 10px;
    overflow-y: auto;
    overflow-y: auto;
    border-right: 1px solid var(--fg-2);
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  /* .session-item {
    padding: 10px;
    margin-bottom: 5px;
    cursor: pointer;
    border-radius: 5px;
    transition: background-color 0.3s ease;
  }

  .session-item:hover, .session-item.active {
    background-color: var(--bg-2);
  } */

  .chat-container {
    flex-grow: 1;
    display: flex;
    flex-direction: column;
    margin-left: 20px;
  }

  .chat-area {
    flex-grow: 1;
    overflow-y: auto;
  }

  .error {
    color: var(--error-color);
    text-align: center;
    font-size: 1.2em;
    margin-top: 20px;
  }

  .task-selection {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
  }

  .task-dropdown {
    padding: 10px;
    border-radius: 5px;
    border: 1px solid var(--bg-2);
    background-color: var(--bg-1);
    color: var(--fg-1);
    font-size: 1em;
    inline-size: 100%;
  }

  .task-dropdown:focus {
    outline: none;
    border-color: var(--accent-color);
  }

  .interaction-layout {
    inline-size: 100%;
    display: flex;
    justify-content: space-between;
    gap: 20px;
    height: 100%;
  }

  .main-content {
    inline-size: fit-content;
    flex-grow: 1;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
  }

  @media (max-width: 768px) {
    .interaction-layout {
      flex-direction: column;
    }

    .sessions-sidebar {
      width: 100%;
      max-block-size: 100vh;
      border-right: none;
      border-bottom: 1px solid var(--bg-2);
    }
  }
</style>
