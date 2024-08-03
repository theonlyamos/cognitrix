<script lang="ts">
  import { link, navigate } from "svelte-routing";
  import {
    getTask,
    getTools,
    saveTask,
    getAllAgents,
    updateTaskStatus,
  } from "../common/utils";
  import type {
    TaskDetailInterface,
    ProviderInterface,
    ToolInterface,
    AgentDetailInterface,
  } from "../common/interfaces";
  import { getAllTasks } from "../common/utils";
  import type { TaskInterface } from "../common/interfaces";
  import GenerativeIcon from "../assets/ai-curved-star-icon-multiple.svg";
  import Checkbox from "../lib/Checkbox.svelte";
  import Switch from "../lib/Switch.svelte";
  import Accordion from "../lib/Accordion.svelte";
  import { webSocketStore } from "../common/stores";
  import type { Unsubscriber } from "svelte/motion";
  import { onDestroy, onMount } from "svelte";
  import Tasks from "./Tasks.svelte";

  export let task_id: string = "";
  let agents: AgentDetailInterface[] = [];
  let task: TaskDetailInterface = {
    title: "",
    description: "",
    agent_ids: [],
    tools: [],
    autostart: false,
    status: "not-started",
    done: false,
  };
  let tools: ToolInterface[] = [];
  let taskAgents: string[] = [];
  let taskTools: string[] = [];

  let selectedAgents: string[] = [];
  let selectedTools: string[] = [];
  let loading: boolean = false;
  let submitting: boolean = false;
  let unsubscribe: Unsubscriber | null = null;
  let socket: WebSocket;
  let newTaskDescription: string = "";

  const loadTask = async (task_id: string) => {
    try {
      task = (await getTask(task_id)) as TaskDetailInterface;

      if (task.tools && task.tools.length) {
        taskTools = task.tools.map((t) => t.name);
      }
    } catch (error) {
      console.log(error);
    }
  };

  const generateTaskDescription = () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      webSocketStore.send(
        JSON.stringify({
          type: "generate",
          action: "task_instructions",
          name: task?.title,
          prompt: task?.description,
        })
      );
      loading = true;
      task.description = "";
    }
  };

  const startWebSocketConnection = () => {
    unsubscribe = webSocketStore.subscribe(
      (event: { socket: WebSocket; type: string; data?: any }) => {
        if (event !== null) {
          socket = event.socket;

          if (event.type === "message") {
            let data = JSON.parse(event.data);

            if (data.type == "generate" && data.action == "task_instructions") {
              newTaskDescription = task.description + data.content;
              loading = false;
            }
          }
        }
      }
    );
  };

  onMount(() => {
    startWebSocketConnection();

    return () => {
      if (unsubscribe) unsubscribe();
    };
  });

  (async () => {
    try {
      agents = (await getAllAgents()) as AgentDetailInterface[];
      tools = (await getTools()) as ToolInterface[];
    } catch (error) {
      console.log(error);
    }
  })();

  const handleToolChange = (event: Event) => {
    const target = event.target as HTMLInputElement;
    if (target.checked) {
      if (!selectedTools.includes(target.value))
        selectedTools = [...selectedTools, target.value];
    } else {
      const index = selectedTools.indexOf(target.value);
      if (index > -1) {
        let oldArray = [...selectedTools];
        oldArray.splice(index, 1);
        selectedTools = [...oldArray];
      }
    }

    task.tools = selectedTools
      .map((tool) => tools.find((t) => t.name === tool))
      .filter((tool): tool is ToolInterface => tool !== undefined);
  };

  const handleAgentsChange = (event: Event) => {
    const target = event.target as HTMLInputElement;
    if (target.checked) {
      if (!selectedAgents.includes(target.value))
        selectedAgents = [...selectedAgents, target.value];
    } else {
      const index = selectedAgents.indexOf(target.value);
      if (index > -1) {
        let oldArray = [...selectedAgents];
        oldArray.splice(index, 1);
        selectedAgents = [...oldArray];
      }
    }
  };

  const handleTaskSubmit = async () => {
    try {
      submitting = true;
      task = (await saveTask(task)) as TaskDetailInterface;
      if (task.id) task_id = task.id;
    } catch (error) {
      console.log(error);
    } finally {
      submitting = false;
    }
  };

  const onStartTask = async () => {
    try {
      submitting = true;
      task = (await updateTaskStatus(task?.id)) as TaskDetailInterface;
      if (task.id) task_id = task.id;
    } catch (error) {
      console.log(error);
    } finally {
      submitting = false;
    }
  };

  const stopTask = async () => {
    task.status = "not-started";
    task = (await saveTask(task)) as TaskDetailInterface;
  };

  onDestroy(() => {
    if (unsubscribe) unsubscribe();
  });

  $: if (newTaskDescription) {
    task.description = newTaskDescription;
  }

  $: task.agent_ids = selectedAgents;
  $: console.log(task);
</script>

{#if task_id}
  {#await loadTask(task_id)}
    <div class="container">
      <div class="loading">
        <i class="fas fa-spinner fa-spin fa-3x"></i>
      </div>
    </div>
  {/await}
{/if}
<div class="toolbar">
  {#if task_id}
    <div style="margin-right: auto; display: flex; gap: 10px;">
      <button
        class="btn"
        disabled={task.status === "in-progress"}
        on:click={onStartTask}
      >
        <i class="fa-solid fa-tools fa-fw"></i>
        <span>Start Task</span>
      </button>
      <button
        class="btn"
        disabled={["not-started", "completed"].includes(task.status)}
        on:click={stopTask}
      >
        <i class="fa-solid fa-stop fa-fw"></i>
        <span>Stop Task</span>
      </button>
    </div>
  {/if}
  <button class="btn" disabled={submitting} on:click={handleTaskSubmit}>
    <i class="fa-solid fa-save fa-fw"></i>
    <span
      >{submitting ? "Saving..." : task_id ? "Update Task" : "Save Task"}</span
    >
  </button>
</div>
<div class="container ready">
  <div class="task-form">
    <div class="form-group">
      <label for="name">Task Title</label>
      <input
        type="text"
        bind:value={task.title}
        placeholder="Descriptive title for the task."
      />
    </div>
    <!-- <div class="form-group">
            <Switch label="Is Sub Task" bind:checked={task.is_sub_task} />
        </div> -->
  </div>
  <div class="task-form">
    <div class="form-group">
      <label for="prompt">Task Description</label>
      <textarea
        rows="15"
        bind:value={task.description}
        placeholder="Provide a brief description of the task and click the Generate Description button to generate a detailed step-by-step instructions for completing the task."
      ></textarea>
    </div>
  </div>
  <button
    class="btn ai-generate"
    on:click={generateTaskDescription}
    disabled={task.description === "" || loading}
  >
    <img src={GenerativeIcon} alt="generative" class="icon" />
    {#if loading}
      <i class="fas fa-spinner fa-spin"></i>
    {:else}
      <span>Generate Task Description & Instructions</span>
    {/if}
  </button>
  <div class="task-form tools">
    <Accordion title="Select Agents">
      <div class="tools-list">
        {#each agents as agent, index (agent?.id)}
          <Checkbox
            name="tools"
            value={agent.id}
            label={agent.name}
            onChange={handleAgentsChange}
            checked={task.agent_ids.includes(agent?.id)}
          />
        {/each}
      </div>
    </Accordion>
  </div>
  <div class="task-form tools">
    <Accordion title="Tools">
      <div class="tools-list">
        {#each tools as tool, index (index)}
          <Checkbox
            name="tools"
            value={tool.name}
            label={tool.name}
            onChange={handleToolChange}
            checked={taskTools.includes(tool.name)}
          />
        {/each}
      </div>
    </Accordion>
  </div>
  <div class="task-form">
    <div class="form-group">
      <Switch label="Autostart" bind:checked={task.autostart} />
    </div>
  </div>
</div>

<style>
  .container {
    inline-size: 700px;
    max-inline-size: 100%;
    padding-inline: 20px;
    padding-block: 20px;
    display: grid;
    margin-inline: auto;
    margin-block: 0;
  }

  .container.ready {
    gap: 20px;
    overflow-y: auto;
  }

  .toolbar {
    position: sticky;
    inset-inline: 0;
    inset-block: 0;
    background-color: inherit;
  }

  .task-form {
    display: flex;
    flex-direction: column;
    block-size: fit-content;
    gap: 10px;
    background-color: var(--bg-1);
    padding-inline: 20px;
    padding-block: 20px;
    border-radius: 5px;
    box-shadow: var(--shadow-sm);
    position: relative;
  }

  .btn.ai-generate {
    margin-inline-start: auto;
    inline-size: fit-content;
    background-color: var(--bg-1);
    color: var(--fg-1);
    padding-inline: 7px;
    padding-block: 5px;
    border-radius: 7px;
    display: flex;
    align-items: center;
    gap: 2px;
    box-shadow: var(--shadow-sm);
  }

  .tools {
    z-index: 10;
  }

  .tools-list {
    inline-size: 100%;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
  }

  textarea {
    resize: none;
  }

  img.icon {
    inline-size: 30px;
    block-size: 30px;
    filter: invert(1);
  }
</style>
