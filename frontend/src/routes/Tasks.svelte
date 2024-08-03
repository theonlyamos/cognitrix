<script lang="ts">
  import { link } from "svelte-routing";
  import { getAllTasks } from "../common/utils";
  import type { TaskInterface } from "../common/interfaces";
  import TaskCard from "../lib/TaskCard.svelte";

  let tasks: TaskInterface[] = [];

  const loadTasks = async () => {
    try {
      tasks = (await getAllTasks()) as TaskInterface[];
      tasks = tasks.reverse();
      console.log(tasks);
    } catch (error) {
      console.log(error);
    }
  };
</script>

{#await loadTasks()}
  <div class="loading">
    <i class="fas fa-spinner fa-spin fa-3x"></i>
  </div>
{:then}
  <div class="toolbar">
    <a href="/tasks/new" use:link class="btn">
      <i class="fa-solid fa-tools fa-fw"></i>
      <span>New Task</span>
    </a>
  </div>
  <div class="container">
    <div class="tasks-container">
      {#each tasks as task (task?.id)}
        <TaskCard {task} />
      {/each}
    </div>
  </div>
{/await}

<style>
  .tasks-container {
    padding-inline: 20px;
    padding-block: 20px;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }
</style>
