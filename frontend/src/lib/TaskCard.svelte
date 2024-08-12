<script lang="ts">
  import { link } from "svelte-routing";
  import type { TaskDetailInterface } from "../common/interfaces";
  import RadioItem from "./RadioItem.svelte";

  export let task: TaskDetailInterface;

  function calculateDuration(startedAt: string, completedAt: string) {
    const startDate = new Date(startedAt);
    const endDate = new Date(completedAt);

    const durationMs = endDate - startDate;

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
</script>

<a
  href={`/tasks/${task?.id}`}
  use:link
  class="task-card"
  id={`task-${task?.id}`}
  data-id={task?.id}
>
  <div class="task-card__title">
    {task.title}
  </div>
  {#if task.step_instructions && Object.keys(task.step_instructions).length > 0}
    <div class="task-card__step-instructions">
      {#each Object.entries(task.step_instructions) as [index, value]}
        <RadioItem
          name="index"
          label={value["step"]}
          disabled={true}
          checked={value["done"]}
        />
      {/each}
    </div>
  {:else}
    <div class="task-card__description">
      {task.description}
    </div>
  {/if}
  <div class="task-card-footer">
    <div class="task-card-footer__status">
      {task.status}
    </div>
    <div class="task-card-footer__duration">
      {#if task.status === "completed"}
        <i class="fa-solid fa-clock fa-fw"></i>
        <span>{calculateDuration(task.started_at, task.completed_at)}</span>
      {/if}
    </div>
  </div>
</a>

<style>
  .task-card {
    background: linear-gradient(to bottom, var(--bg-1), var(--bg-1));
    color: var(--fg-1);
    border-radius: 10px;
    padding: 20px;
    inline-size: 250px;
    max-inline-size: 100%;
    block-size: 300px;
    box-shadow:
      0 10px 20px rgba(0, 0, 0, 0.3),
      0 6px 6px rgba(0, 0, 0, 0.2),
      inset 0 1px 1px rgba(255, 255, 255, 0.1);
    position: relative;
    overflow: hidden;
    text-align: start;

    &:hover {
      box-shadow:
        0 5px 10px rgba(0, 0, 0, 0.3),
        0 6px 6px rgba(0, 0, 0, 0.2),
        inset 0 1px 1px rgba(255, 255, 255, 0.1);
    }
  }
  /* .task-card::before {
    content: "";
    position: absolute;
    inset-block-start: 0;
    inset-inline-start: -50%;
    inline-size: 200%;
    block-size: 200%;
    background: radial-gradient(circle, var(--bg-2) 0%, var(--bg-1) 60%);
    opacity: 0.5;
  } */

  .task-card__title {
    font-weight: bold;
    margin-bottom: 10px;
    position: relative;
  }
  .task-card__step-instructions {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .task-card__description {
    text-align: justify;
    font-size: 1.7vmin;
    margin-bottom: 15px;
    position: relative;
    color: var(--fg-2);
    display: -webkit-box;
    -webkit-line-clamp: 8; /* Change this number to set the number of lines */
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .task-card__description,
  .task-card__step-instructions {
    max-block-size: 75%;
    overflow-y: auto;
    padding-block-end: 10px;

    &::-webkit-scrollbar {
      width: 5px;
    }
  }

  .task-card-footer {
    position: absolute;
    inset-block-end: 0;
    inset-inline-start: 0;
    inline-size: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: inherit;
    background: inherit;
  }

  .task-card-footer__status {
    padding-block: 5px;
    padding-inline: 10px;
    font-size: 1.4vmin;
    font-weight: bold;
    border-radius: 10px;
    background-color: var(--bg-2);
  }

  .task-card-footer__duration {
    font-size: 1.4vmin;
  }
</style>
