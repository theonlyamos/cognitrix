<script lang="ts">
  import { link } from "svelte-routing";
  import { deleteTask, getAllTasks, getAllTeams, getTasksByTeam } from "../common/utils";
  import type { TaskDetailInterface, TaskInterface, TeamInterface } from "../common/interfaces";
  import TaskCard from "../lib/TaskCard.svelte";
  import Select from "$lib/Select.svelte";
    import Modal from "$lib/Modal.svelte";

  let tasks: TaskInterface[] = [];
  let teams: TeamInterface[] = [];
  let selectedTeam: string = "";

  let confirmModal = false;
  let taskToDelete: string = "";
  let confirmTitle: string = "Delete this task!";

  const loadTasks = async () => {
    try {
      tasks = (await getAllTasks()) as TaskInterface[];
      teams = (await getAllTeams()) as TeamInterface[];
      tasks = tasks.reverse();
      console.log(tasks);
    } catch (error) {
      console.log(error);
    }
  };

  const filterTasksByTeam = async () => {
    if (selectedTeam) {
      tasks = await getTasksByTeam(selectedTeam);
    } else {
      tasks = (await getAllTasks()) as TaskInterface[];
    }
    tasks = tasks.reverse();
  };

  const onDeleteTask = async(task: TaskDetailInterface)=>{
    confirmModal = true;
    taskToDelete = task.id as string;
  }

  const onConfirmDeleteTask = async()=>{
    try{
      await deleteTask(taskToDelete);
      tasks = tasks.filter((t) => t.id !== taskToDelete);
    } catch(error){
      console.log(error);
    } finally {
      confirmModal = false;
    }
  }

  $: if (selectedTeam !== undefined) {
    filterTasksByTeam();
  }
</script>

<Modal
  isOpen={confirmModal}
  type="alert"
  action={onConfirmDeleteTask}
  title={confirmTitle}
  actionLabel="Yes, do it"
  size="small"
  appearance="bordered"
  onClose={() => {
    confirmModal = false;
  }}
>
  Are you sure you want to proceed?
</Modal>

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
    <div class="filter-container">
      <Select
        options={[
          { value: "", label: "All Teams" },
          ...teams.map((team) => ({
            value: team.id || "",
            label: team.name,
          })),
        ]}
        bind:value={selectedTeam}
        placeholder="Filter tasks by team"
      />
    </div>
    <div class="tasks-container">
      {#each tasks as task (task?.id)}
        <TaskCard {task} onDelete={onDeleteTask} />
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

  .filter-container {
    padding-inline: 20px;
    padding-block: 20px;
    max-width: 300px;
  }
</style>
