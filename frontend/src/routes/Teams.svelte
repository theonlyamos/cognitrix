<script lang="ts">
  import { link } from "svelte-routing";
  import { getAllTeams } from "../common/utils";
  import type { TeamInterface } from "../common/interfaces";
  import TeamCard from "../lib/TeamCard.svelte";
  import { onMount } from "svelte";

  let teams: TeamInterface[] = $state([]);

  const loadTeams = async () => {
    try {
      teams = (await getAllTeams()) as TeamInterface[];
      console.log(teams);
    } catch (error) {
      console.log(error);
    }
  };

  //   onMount(async () => {
  //     await loadTeams();
  //   });
</script>

{#await loadTeams()}
  <div class="loading">
    <i class="fas fa-spinner fa-spin fa-3x"></i>
  </div>
{:then}
  <div class="toolbar">
    <a href="/teams/new" use:link class="btn">
      <i class="fa-solid fa-users fa-fw"></i>
      <span>New Team</span>
    </a>
  </div>
  <div class="container">
    <div class="teams-container">
      {#each teams as team (team?.id)}
        <TeamCard {team} />
      {/each}
    </div>
  </div>
{/await}

<style>
  .teams-container {
    padding-inline: 20px;
    padding-block: 20px;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }
</style>
