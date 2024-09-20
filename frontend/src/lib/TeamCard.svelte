<script lang="ts">
  import { link } from "svelte-routing";
  import type { TeamInterface } from "../common/interfaces";

  export let team: TeamInterface;
</script>

<a href="/teams/{team.id}" use:link class="card team-card">
  <div class="team-card__header">
    <i class="fa-solid fa-users fa-3x"></i>
    <h3>{team.name}</h3>
    <p>Agents: {team.assigned_agents.length}</p>
  </div>
  <div class="team-card__description">
    {team.description}
  </div>
</a>

<style>
  .team-card {
    background-color: var(--bg-1);
    padding: 20px;
    border-radius: 10px;
    box-shadow: var(--shadow-sm);
    width: 250px;
    /* text-align: start; */
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .team-card__header {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .team-card__header i {
    text-align: center;
  }

  .team-card__description {
    text-align: center;
    font-size: 1.9vmin;
    margin-bottom: 15px;
    position: relative;
    color: var(--fg-2);
    display: -webkit-box;
    line-clamp: 4;
    -webkit-line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  h3 {
    margin-top: 0;
  }
</style>
