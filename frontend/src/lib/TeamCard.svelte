<script lang="ts">
  import { link } from "svelte-routing";
  import type { TeamInterface } from "../common/interfaces";

  export let team: TeamInterface;
</script>

// src/lib/TeamCard.svelte
<div class="team-card">
  <h3>{team.name}</h3>
  <p>Agents: {team.agent_ids.length}</p>
  <a href="/teams/{team.id}" use:link class="btn">View Team</a>
</div>

<style>
  .team-card {
    background-color: var(--bg-1);
    padding: 20px;
    border-radius: 10px;
    box-shadow: var(--shadow-sm);
    width: 250px;
  }

  h3 {
    margin-top: 0;
  }
</style>
