<script lang="ts">
  import { link } from "svelte-routing";
  import type { TaskDetailInterface } from "../common/interfaces";
  import RadioItem from "./RadioItem.svelte";

  export let task: TaskDetailInterface;

  function calculateDuration(startedAt: string, completedAt: string) {
    const startDate: Date = new Date(startedAt);
    const endDate: Date = new Date(completedAt);

    const durationMs = endDate.getTime() - startDate.getTime();

    const hours = Math.floor(durationMs / 3600000);
    const minutes = Math.floor((durationMs % 3600000) / 60000);
    const seconds = Math.floor((durationMs % 60000) / 1000);

    // Construct the duration string
    let durationStr = "";
    if (hours > 0) durationStr += `${hours} hr${hours !== 1 ? "s" : ""} `;
    if (minutes > 0) durationStr += `${minutes}min `;
    if (seconds > 0) durationStr += ` ${seconds}s`;

    return durationStr.trim();
  }

  function handleEdit() {
    console.log(`Edit task ${task.id}`);
  }

  function handleDelete() {
    console.log(`Delete task ${task.id}`);
  }

  function handleRun() {
    console.log(`Run task ${task.id}`);
  }

  function formatDate(dateString: string) {
    return new Date(dateString).toLocaleDateString();
  }

  function getShortDescription(description: string, maxLength: number = 50) {
    return description && description.length > maxLength 
      ? description.slice(0, maxLength) + '...' 
      : description;
  }
</script>

<div class="card task-card" id={`task-${task?.id}`} data-id={task?.id}>
  <a href={`/tasks/${task?.id}`} use:link class="task-info">
    <h3 class="task-name">{task.title}</h3>
    <p class="task-description">
      {getShortDescription(task.description)}
    </p>
    <div class="task-details">
      <div class="task-detail">
        <i class="fas fa-tasks"></i>
        <span>{task.status}</span>
      </div>
      <div class="task-detail">
        <i class="fas fa-users"></i>
        <span
          >{task.assigned_agents.length} agent{task.assigned_agents.length !== 1
            ? "s"
            : ""}</span
        >
      </div>
      {#if task.tools && task.tools.length > 0}
        <div class="task-detail">
          <i class="fas fa-tools"></i>
          <span
            >{task.tools
              .slice(0, 3)
              .map((tool) => tool.name)
              .join(", ")}...</span
          >
        </div>
      {/if}
      {#if task.created_at}
        <div class="task-detail">
          <i class="fas fa-calendar-alt"></i>
          <span>Created: {formatDate(task.created_at)}</span>
        </div>
      {/if}
      {#if task.status === "completed" && task.started_at && task.completed_at}
        <div class="task-detail">
          <i class="fas fa-clock"></i>
          <span
            >Duration: {calculateDuration(
              task.started_at,
              task.completed_at,
            )}</span
          >
        </div>
      {/if}
    </div>
  </a>
  <div class="task-options">
    <button on:click={handleEdit} class="option-button edit">
      <i class="fas fa-edit"></i> Edit
    </button>
    <button on:click={handleDelete} class="option-button delete">
      <i class="fas fa-trash-alt"></i> Delete
    </button>
    <button on:click={handleRun} class="option-button run">
      <i class="fas fa-play"></i> Run
    </button>
  </div>
</div>

<style>
  .task-card {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 20px;
    border-radius: 12px;
    background: var(--bg-1);
    color: var(--fg-1);
    min-inline-size: 300px;
    max-inline-size: 100vmin;
    block-size: auto;
    box-shadow: 0 10px 20px rgba(0, 0, 0, 0.1);
    transition: all 0.3s ease;
    overflow: hidden;
  }

  .task-card:hover {
    transform: translateY(-5px);
    box-shadow: 0 15px 30px rgba(0, 0, 0, 0.15);
  }

  .task-info {
    display: flex;
    flex-direction: column;
    gap: 15px;
    margin-bottom: 20px;
    text-decoration: none;
    color: inherit;
  }

  .task-name {
    font-size: 1.5em;
    font-weight: 600;
    margin: 0;
    color: var(--accent-color, #2196F3);
  }

  .task-description {
    font-size: 0.9em;
    color: var(--fg-2);
    margin: 0;
  }

  .task-details {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .task-detail {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.9em;
  }

  .task-detail i {
    width: 20px;
    text-align: center;
    color: var(--accent-color, #2196F3);
  }

  .task-card__title {
    font-size: 1.3em;
    font-weight: 600;
    margin-bottom: 10px;
    color: var(--accent-color, #2196F3);
  }

  .task-card__step-instructions {
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-block-size: 75%;
    overflow-y: auto;
    padding-block-end: 10px;
    flex-grow: 1;
  }

  .task-card__description {
    text-align: justify;
    font-size: 0.9em;
    color: var(--fg-2);
    display: -webkit-box;
    line-clamp: 8;
    -webkit-line-clamp: 8;
    -webkit-box-orient: vertical;
    overflow: hidden;
    max-block-size: 75%;
    overflow-y: auto;
    padding-block-end: 10px;
    flex-grow: 1;
  }

  .task-card__step-instructions::-webkit-scrollbar,
  .task-card__description::-webkit-scrollbar {
    width: 5px;
  }

  .task-card-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: auto;
    padding-top: 10px;
  }

  .task-card-footer__status {
    padding-block: 5px;
    padding-inline: 10px;
    font-size: 0.9em;
    font-weight: bold;
    border-radius: 10px;
    background-color: var(--bg-2);
  }

  .task-card-footer__duration {
    font-size: 0.9em;
    display: flex;
    align-items: center;
    gap: 5px;
  }

  .task-card-footer__duration i {
    color: var(--accent-color, #2196F3);
  }

  .task-options {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin-top: 15px;
  }

  .option-button {
    flex: 1;
    padding: 8px 12px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 0.9em;
    transition: all 0.3s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  }

  .edit { background-color: #4CAF50; color: white; }
  .delete { background-color: #f44336; color: white; }
  .run { background-color: #2196F3; color: white; }

  .option-button:hover {
    opacity: 0.9;
    transform: translateY(-2px);
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
  }

  .option-button:active {
    transform: translateY(0);
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  }

  .option-button i {
    font-size: 1em;
  }
</style>
