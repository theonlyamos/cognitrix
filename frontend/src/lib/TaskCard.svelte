<script lang="ts">
  import { link } from "svelte-routing";
  import type { TaskInterface } from "../common/interfaces";
  import { onMount } from "svelte";

  export let task: TaskInterface;
</script>

<a
  href={`/tasks/${task?.id}`}
  use:link
  class="task-card"
  id={`task-${task?.id}`}
  data-id={task?.id}
>
  <div class="task-detail">
    <div class="task-detail-value">
      {task.title}
    </div>
  </div>

  <div class="task-detail">
    <div class="task-detail-key">Status:</div>
    <div class="task-detail-value">
      {task.status}
    </div>
  </div>

  <div class="task-detail">
    <div class="task-detail-key">Description:</div>
    <div class="task-detail-value">
      {task.description}
    </div>
  </div>
</a>

<style>
  .task-card {
    text-align: start;
    padding-inline: 20px;
    padding-block: 20px;
    border-radius: 4px;
    background-color: var(--bg-1);
    color: var(--fg-2);
    min-inline-size: 250px;
    display: grid;

    &:hover {
      box-shadow: var(--shadow-sm);
      color: var(--fg-1);
    }
  }

  .task-detail {
    inline-size: 100%;
    text-align: start;
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    padding: 10px;
    border-block-end: 1px solid var(--fg-2);

    &:last-of-type {
      border-block-end: none;
    }
  }

  .task-detail-value {
    display: -webkit-box;
    -webkit-line-clamp: 5; /* Change this number to set the number of lines */
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
</style>
