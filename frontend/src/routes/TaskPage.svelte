<script lang="ts">
  import { run } from "svelte/legacy";

  import {
    getTask,
    getTools,
    saveTask,
    getAllAgents,
    updateTaskStatus,
    getAllTeams,
    getTasksByTeam,
  } from "../common/utils";
  import type {
    TaskDetailInterface,
    ToolInterface,
    AgentDetailInterface,
    TeamInterface,
  } from "../common/interfaces";
  import Checkbox from "../lib/Checkbox.svelte";
  import Switch from "../lib/Switch.svelte";
  import Accordion from "../lib/Accordion.svelte";
  import Select from "$lib/Select.svelte";
  import Modal from "../lib/Modal.svelte";
  import { onMount, onDestroy } from "svelte";
  import { webSocketStore } from "../common/stores";
  import type { Unsubscriber } from "svelte/store";
  import Alert from "../lib/Alert.svelte";

  interface Props {
    task_id?: string;
  }

  let { task_id = $bindable("") }: Props = $props();
  let agents: AgentDetailInterface[] = $state([]);
  let task: TaskDetailInterface = $state({
    title: "",
    description: "",
    assigned_agents: [],
    tools: [],
    autostart: false,
    status: "pending",
    done: false,
    team_id: "",
  });
  let tools: ToolInterface[] = $state([]);
  let taskAgents: string[] = [];
  let taskTools: string[] = $state([]);

  let selectedAgents: string[] = $state([]);
  let selectedTools: string[] = [];
  let selectedTeam: string = $state("");
  let loading: boolean = false;
  let submitting: boolean = $state(false);
  let unsubscribe: Unsubscriber | null = null;
  let socket: WebSocket;
  let newTaskDescription: string = $state("");
  let isGenerativeMode = $state(false);
  let generativeDescription = $state("");

  let teams: TeamInterface[] = $state([]);

  let alertMessage = $state("");
  let alertType: "default" | "success" | "warning" | "danger" | "loading" =
    $state("default");
  let showAlert = $state(false);

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
        }),
      );
      loading = true;
      task.description = "";
    }
  };

  const handleModeChange = (event: CustomEvent<boolean>) => {
    isGenerativeMode = event.detail;
  };

  const generateTaskDetails = () => {
    if (
      socket &&
      socket.readyState === WebSocket.OPEN &&
      generativeDescription
    ) {
      webSocketStore.send(
        JSON.stringify({
          type: "generate",
          action: "team_details",
          prompt: generativeDescription,
        }),
      );
      loading = true;
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
      },
    );
  };

  (async () => {
    try {
      agents = (await getAllAgents()) as AgentDetailInterface[];
      tools = (await getTools()) as ToolInterface[];
      teams = (await getAllTeams()) as TeamInterface[];
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
      alertType = "loading";
      alertMessage = "Saving task...";
      showAlert = true;

      task.team_id = selectedTeam;
      task = (await saveTask(task)) as TaskDetailInterface;
      if (task.id) task_id = task.id;
      alertType = "success";
      alertMessage = `Task ${task_id ? "updated" : "created"} successfully!`;
    } catch (error) {
      console.log(error);
      alertType = "danger";
      alertMessage = `Failed to ${task_id ? "update" : "create"} task. Please try again.`;
    } finally {
      submitting = false;
    }
  };

  const onStartTask = async () => {
    try {
      submitting = true;
      alertType = "loading";
      alertMessage = "Starting task...";
      showAlert = true;

      task = (await updateTaskStatus(task?.id)) as TaskDetailInterface;
      if (task.id) task_id = task.id;
      alertType = "success";
      alertMessage = "Task started successfully!";
    } catch (error) {
      console.log(error);
      alertType = "danger";
      alertMessage = "Failed to start task. Please try again.";
    } finally {
      submitting = false;
    }
  };

  const stopTask = async () => {
    try {
      alertType = "loading";
      alertMessage = "Stopping task...";
      showAlert = true;

      task.status = "not-started";
      task = (await saveTask(task)) as TaskDetailInterface;
      alertType = "success";
      alertMessage = "Task stopped successfully!";
    } catch (error) {
      console.log(error);
      alertType = "danger";
      alertMessage = "Failed to stop task. Please try again.";
    }
  };

  onMount(() => {
    startWebSocketConnection();

    return () => {
      if (unsubscribe) unsubscribe();
    };
  });

  onDestroy(() => {
    if (unsubscribe) unsubscribe();
  });

  run(() => {
    if (newTaskDescription) {
      task.description = newTaskDescription;
    }
  });

  run(() => {
    task.assigned_agents = selectedAgents;
  });

  run(() => {
    if (task.team_id) {
      selectedTeam = task.team_id;
    }
  });
</script>

<Modal
  isOpen={isGenerativeMode}
  type="info"
  action={generateTaskDetails}
  actionLabel="Generate Task Details"
  size="medium"
  appearance="floating"
  title="Generate Task Details"
  onClose={() => {
    isGenerativeMode = false;
  }}
>
  <div class="form-group">
    <textarea
      id="generativeDescription"
      rows="10"
      bind:value={generativeDescription}
      placeholder="Provide a brief description of the task and click the Generate Task Details button to generate a detailed step-by-step instructions for completing the task."
    ></textarea>
  </div>
</Modal>

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
        onclick={onStartTask}
      >
        <i class="fa-solid fa-tools fa-fw"></i>
        <span>Start Task</span>
      </button>
      <button
        class="btn"
        disabled={["not-started", "completed"].includes(task.status)}
        onclick={stopTask}
      >
        <i class="fa-solid fa-stop fa-fw"></i>
        <span>Stop Task</span>
      </button>
    </div>
  {/if}
  <button class="btn" disabled={submitting} onclick={handleTaskSubmit}>
    <i class="fa-solid fa-save fa-fw"></i>
    <span
      >{submitting ? "Saving..." : task_id ? "Update Task" : "Save Task"}</span
    >
  </button>
</div>
<div class="container ready">
  <div class="task-form">
    <div class="form-group mode-switch">
      <Switch
        label="Generative Mode"
        name="generativeMode"
        bind:checked={isGenerativeMode}
        onChange={handleModeChange}
      />
    </div>
  </div>
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
        placeholder="Provide a detailed description of the task."
      ></textarea>
    </div>
  </div>
  <div class="task-form tools">
    <Accordion title="Select Agents">
      <div class="tools-list">
        {#each agents as agent, index (agent?.id)}
          <Checkbox
            name="tools"
            value={agent.id}
            label={agent.name}
            onChange={handleAgentsChange}
            checked={agent?.id
              ? task.assigned_agents.includes(agent?.id)
              : false}
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
  <div class="task-form">
    <div class="form-group">
      <label for="team">Assign to Team</label>
      <Select
        options={teams.map((team) => ({
          value: team.id || "",
          label: team.name,
        }))}
        bind:value={selectedTeam}
        placeholder="Select a team"
      />
    </div>
  </div>
</div>

{#if showAlert}
  <Alert
    type={alertType}
    message={alertMessage}
    onClose={() => (showAlert = false)}
    autoClose={alertType !== "loading"}
  />
{/if}

<style>
  .container {
    inline-size: 700px;
    max-inline-size: 100%;
    block-size: fit-content;
    padding-inline: 20px;
    padding-block: 20px;
    display: grid;
    margin-inline: auto;
    margin-block: 0;
    gap: 20px;
  }

  .container.ready {
    gap: 20px;
    overflow-y: auto;
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
</style>
