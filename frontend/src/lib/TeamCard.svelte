<script lang="ts">
  import { link } from "svelte-routing";
  import type { TeamInterface } from "../common/interfaces";

  interface Props {
    team: TeamInterface;
  }

  let { team }: Props = $props();

  function handleEdit() {
    console.log(`Edit team ${team.id}`);
  }

  function handleDelete() {
    console.log(`Delete team ${team.id}`);
  }

  function handleManage() {
    console.log(`Manage team ${team.id}`);
  }

  function handleTask() {
    console.log(`Assign task to team ${team.id}`);
  }

  function handleInteract() {
    console.log(`Interact with team ${team.id}`);
  }

  function formatDate(dateString: string) {
    return new Date(dateString).toLocaleDateString();
  }

  function truncateDescription(description: string, maxLength: number = 200) {
    return description.length > maxLength
      ? description.slice(0, maxLength) + '...'
      : description;
  }
</script>

<div class="card team-card">
  <a href="/teams/{team.id}" use:link class="team-info">
    <div class="team-card__header">
      <i class="fa-solid fa-users fa-3x"></i>
      <h3 class="team-name">{team.name}</h3>
    </div>
    <div class="team-card__description">
      {truncateDescription(team.description)}
    </div>
    <div class="team-details">
      <div class="team-detail">
        <i class="fas fa-user-friends"></i>
        <span>Agents: {team.assigned_agents.length}</span>
      </div>
      {#if team.created_at}
        <div class="team-detail">
          <i class="fas fa-calendar-plus"></i>
          <span>Created: {formatDate(team.created_at)}</span>
        </div>
      {/if}
    </div>
  </a>
  <div class="team-options">
    <button onclick={handleManage} class="option-button manage">
      <i class="fas fa-cogs"></i> Manage
    </button>
    <button onclick={handleDelete} class="option-button delete">
      <i class="fas fa-trash-alt"></i> Delete
    </button>
    <button onclick={handleTask} class="option-button task">
      <i class="fas fa-tasks"></i> Task
    </button>
    <a href="/teams/{team.id}/interact" use:link class="option-button interact">
      <i class="fas fa-comments"></i> Interact
    </a>
  </div>
</div>

<style>
  .team-card {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 20px;
    border-radius: 12px;
    background: var(--bg-1);
    color: var(--fg-1);
    box-shadow: 0 10px 20px rgba(0, 0, 0, 0.1);
    transition: all 0.3s ease;
    overflow: hidden;
    inline-size: 420px;
    max-inline-size: 100vmin;
    block-size: auto;
  }

  .team-card:hover {
    transform: translateY(-5px);
    box-shadow: 0 15px 30px rgba(0, 0, 0, 0.15);
  }

  .team-info {
    text-decoration: none;
    color: inherit;
  }

  .team-card__header {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    margin-bottom: 15px;
  }

  .team-card__header i {
    text-align: center;
    color: var(--accent-color, #2196F3);
  }

  .team-name {
    font-size: 1.5em;
    font-weight: 600;
    margin: 0;
    color: var(--accent-color, #2196F3);
  }

  .team-card__description {
    text-align: center;
    font-size: 0.9em;
    color: var(--fg-2);
    margin-bottom: 15px;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
  }

  .team-details {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .team-detail {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    font-size: 0.9em;
  }

  .team-detail i {
    width: 20px;
    text-align: center;
    color: var(--accent-color, #2196F3);
    margin-top: 3px;
  }

  .team-options {
    display: flex;
    justify-content: space-between;
    gap: 5px;
    margin-top: 15px;
    /* flex-wrap: wrap; */
  }

  .option-button {
    padding: 8px 12px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 0.9em;
    transition: all 0.3s ease;
    display: flex;
    flex-wrap: wrap;
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
  .manage { background-color: #2196F3; color: white; }
  .task { background-color: #FF9800; color: white; }
  .interact { background-color: #9C27B0; color: white; }

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
