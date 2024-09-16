<script lang="ts">
  import { link, navigate } from "svelte-routing";
  import { getTeam, saveTeam, deleteTeam, getAllAgents } from "../common/utils";
  import type { TeamInterface, AgentInterface } from "../common/interfaces";
  import Checkbox from "../lib/Checkbox.svelte";
  import { onMount } from "svelte";
  import Accordion from "../lib/Accordion.svelte";
  import RadioItem from "../lib/RadioItem.svelte";

  export let team_id: string = "";
  let team: TeamInterface = {
    name: "",
    assigned_agents: [],
    description: "",
    team_leader_id: "",
  };
  let agents: AgentInterface[] = [];
  let selectedAgents: string[] = [];

  const loadTeam = async (team_id: string) => {
    try {
      team = (await getTeam(team_id)) as TeamInterface;
      selectedAgents = [...team.assigned_agents];
    } catch (error) {
      console.log(error);
    }
  };

  const handleTeamSubmit = async () => {
    try {
      team.assigned_agents = selectedAgents;
      team = (await saveTeam(team)) as TeamInterface;
      if (team.id) team_id = team.id;
    } catch (error) {
      console.log(error);
    }
  };

  const handleDeleteTeam = async () => {
    if (confirm("Are you sure you want to delete this team?")) {
      try {
        await deleteTeam(team_id);
        navigate("/teams");
      } catch (error) {
        console.log(error);
      }
    }
  };

  const handleAgentChange = (event: Event) => {
    const target = event.target as HTMLInputElement;
    if (target.checked) {
      if (!selectedAgents.includes(target.value))
        selectedAgents = [...selectedAgents, target.value];
    } else {
      selectedAgents = selectedAgents.filter((id) => id !== target.value);
    }
  };

  const handleTeamLeaderChange = (event: Event) => {
    const target = event.target as HTMLInputElement;
    team.team_leader_id = target.value;
  };

  const loadAgents = async () => {
    try {
      agents = (await getAllAgents()) as AgentInterface[];
      console.log(agents);
    } catch (error) {
      console.log(error);
    }
  };

  onMount(async () => {
    await loadAgents();
    selectedAgents = [...team.assigned_agents];
  });

  $: if (team_id) {
    loadTeam(team_id);
  }

  $: {
    team.assigned_agents = selectedAgents;
  }
</script>

{#if team_id}
  {#await loadTeam(team_id)}
    <div class="container">
      <div class="loading">
        <i class="fas fa-spinner fa-spin fa-3x"></i>
      </div>
    </div>
  {/await}
{/if}

<div class="toolbar">
  <button class="btn" on:click={handleTeamSubmit}>
    <i class="fa-solid fa-save fa-fw"></i>
    <span>{team_id ? "Update Team" : "Save Team"}</span>
  </button>
  {#if team_id}
    <button class="btn delete" on:click={handleDeleteTeam}>
      <i class="fa-solid fa-trash fa-fw"></i>
      <span>Delete Team</span>
    </button>
  {/if}
</div>

<div class="container">
  <div class="card team-form">
    <div class="form-group">
      <label for="name">Team Name</label>
      <input type="text" id="name" bind:value={team.name} />
    </div>
    <div class="form-group">
      <label for="description">Team Description</label>
      <textarea
        rows="10"
        bind:value={team.description}
        placeholder="A brief description of the team's purpose or role."
      ></textarea>
    </div>
    <div class="form-group">
      <Accordion title="Select Agents" opened={true}>
        <div class="agents-list">
          {#each agents as agent (agent.id)}
            <Checkbox
              name="agents"
              value={agent.id}
              label={agent.name}
              onChange={handleAgentChange}
              checked={selectedAgents.includes(agent.id)}
            />
          {/each}
        </div>
      </Accordion>
    </div>
    <div class="form-group">
      <Accordion title="Team Leader" opened={true}>
        <div class="agents-list">
          {#each agents as agent (agent.id)}
            {#if team.assigned_agents.includes(agent.id)}
              <RadioItem
                name="agents"
                value={agent.id}
                label={agent.name}
                onChange={handleTeamLeaderChange}
                checked={agent.id === team.team_leader_id}
              />
            {/if}
          {/each}
        </div>
      </Accordion>
    </div>
  </div>
</div>

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
  }

  .team-form {
    background-color: var(--bg-1);
    padding: 20px;
    border-radius: 10px;
  }

  .form-group {
    margin-bottom: 20px;
  }

  label {
    display: block;
    margin-bottom: 5px;
  }

  input[type="text"] {
    width: 100%;
  }

  .delete {
    background-color: var(--error);
  }

  .agents-list {
    inline-size: 100%;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
  }
</style>
