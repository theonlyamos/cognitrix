<script lang="ts">
  import { run } from 'svelte/legacy';

  import { link, navigate } from "svelte-routing";
  import { onMount, onDestroy } from "svelte";
  import { getTeam, saveTeam, deleteTeam, getAllAgents, generateTeam, convertXmlToJson } from "../common/utils";
  import type { TeamInterface, AgentInterface } from "../common/interfaces";
  import Checkbox from "../lib/Checkbox.svelte";
  import { webSocketStore } from "../common/stores";
  import type { Unsubscriber } from "svelte/motion";
  import Accordion from "../lib/Accordion.svelte";
  import RadioItem from "../lib/RadioItem.svelte";
  import Switch from "../lib/Switch.svelte";
  import Modal from "$lib/Modal.svelte";
  import Alert from "../lib/Alert.svelte";

  interface Props {
    team_id?: string;
  }

  let { team_id = $bindable("") }: Props = $props();
  let team: TeamInterface = $state({
    name: "",
    assigned_agents: [],
    description: "",
    leader_id: "",
  });
  let agents: AgentInterface[] = $state([]);
  let selectedAgents: string[] = $state([]);
  let isGenerativeMode = $state(false);
  let generativeDescription = $state("");
  let teamDetails = $state("");
  let teamDetailsLoading = false;
  let unsubscribe: Unsubscriber | null = null;
  let socket: WebSocket;
  let loading = false;

  let alertMessage = $state("");
  let alertType: "default" | "success" | "warning" | "danger" | "loading" = $state("default");
  let showAlert = $state(false);

  const loadTeam = async (team_id: string) => {
    try {
      team = (await getTeam(team_id)) as TeamInterface;
      selectedAgents = [...team.assigned_agents];
      isGenerativeMode = false;
    } catch (error) {
      console.log(error);
    }
  };

  const handleTeamSubmit = async () => {
    try {
      alertType = "loading";
      alertMessage = "Saving team...";
      showAlert = true;

      team.assigned_agents = selectedAgents;
      team = (await saveTeam(team)) as TeamInterface;
      if (team.id) team_id = team.id;

      alertType = "success";
      alertMessage = `Team ${team_id ? "updated" : "created"} successfully!`;
    } catch (error) {
      console.log(error);
      alertType = "danger";
      alertMessage = `Failed to ${team_id ? "update" : "create"} team. Please try again.`;
    }
  };

  const handleDeleteTeam = async () => {
    if (confirm("Are you sure you want to delete this team?")) {
      try {
        alertType = "loading";
        alertMessage = "Deleting team...";
        showAlert = true;

        await deleteTeam(team_id);

        alertType = "success";
        alertMessage = "Team deleted successfully!";
        setTimeout(() => {
          navigate("/teams");
        }, 2000);
      } catch (error) {
        console.log(error);
        alertType = "danger";
        alertMessage = "Failed to delete team. Please try again.";
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
    team.leader_id = target.value;
  };

  const loadAgents = async () => {
    try {
      agents = (await getAllAgents()) as AgentInterface[];
      console.log(agents);
    } catch (error) {
      console.log(error);
    }
  };

  const handleGenerateTeam = async () => {
    try {
      const generatedTeam = await generateTeam(generativeDescription);
      team = { ...team, ...generatedTeam };
      isGenerativeMode = false;
    } catch (error) {
      console.log(error);
    }
  };

  const handleModeChange = (event: CustomEvent<boolean>) => {
    isGenerativeMode = event.detail;
  };

  const generateTeamDetails = () => {
    if (socket && socket.readyState === WebSocket.OPEN && generativeDescription) {
      webSocketStore.send(
        JSON.stringify({
          type: "generate",
          action: "team_details",
          prompt: generativeDescription,
        })
      );
      loading = true;
    }
  };

  const startWebSocketConnection = () => {
    unsubscribe = webSocketStore.subscribe(
      (event: { socket: WebSocket; type: string; data?: any }) => {
        if (event !== null) {
          socket = event.socket;
          if (event.type === "message") {
            loading = false;
            let data = JSON.parse(event.data);

            if (data.type == "generate") {
              loading = false;
              teamDetails = teamDetails + data.content;
            }
          }
        }
      }
    );
  };

  onMount(() => {
    startWebSocketConnection();

    return () => {
      if (unsubscribe) unsubscribe();
    };
  });

  (async () => {
    try {
      await loadAgents();
      selectedAgents = [...team.assigned_agents];
    } catch (error) {
      console.log(error);
    }
  })();

  run(() => {
    if (team_id) {
      loadTeam(team_id);
    }
  });

  run(() => {
    team.assigned_agents = selectedAgents;
  });

  run(() => {
    let parsedTeamDetails: any = convertXmlToJson(teamDetails);
    console.log(parsedTeamDetails);
    team.name = parsedTeamDetails?.name;
    team.description = parsedTeamDetails?.description;
    // team.assigned_agents = parsedTeamDetails.members;
    // team._leader = parsedTeamDetails.leader;
  });
</script>

<Modal
  isOpen={isGenerativeMode}
  type="info"
  action={generateTeamDetails}
  actionLabel="Generate Team Details"
  size="medium"
  appearance="floating"
  title="Generate Team Details"
  onClose={() => {
    isGenerativeMode = false;
  }}
>
  <div class="form-group">
    <textarea
      id="generativeDescription"
      rows="10"
      bind:value={generativeDescription}
      placeholder="Describe the team you want to create, including its name, purpose, and any other relevant details."
    ></textarea>
  </div>
</Modal>

{#if showAlert}
  <Alert
    type={alertType}
    message={alertMessage}
    onClose={() => (showAlert = false)}
    autoClose={alertType !== "loading"}
  />
{/if}

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
  <button class="btn" onclick={handleTeamSubmit}>
    <i class="fa-solid fa-save fa-fw"></i>
    <span>{team_id ? "Update Team" : "Save Team"}</span>
  </button>
  {#if team_id}
    <button class="btn delete" onclick={handleDeleteTeam}>
      <i class="fa-solid fa-trash fa-fw"></i>
      <span>Delete Team</span>
    </button>
  {/if}
</div>

<div class="container">
  <div class="card team-form">
    <div class="form-group mode-switch">
      <Switch
        label="Generative Mode"
        name="generativeMode"
        bind:checked={isGenerativeMode}
        onChange={handleModeChange}
      />
    </div>
  </div>
  <div class="card team-form">
    <div class="form-group">
      <label for="name">Team Name</label>
      <input type="text" id="name" bind:value={team.name} />
    </div>
  </div>
  <div class="card team-form">
    <div class="form-group">
      <label for="description">Team Description</label>
      <textarea
        rows="10"
        bind:value={team.description}
        placeholder="A brief description of the team's purpose or role."
      ></textarea>
    </div>
  </div>
  <div class="card team-form">
    <div class="form-group">
      <Accordion title="Select Agents" opened={false}>
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
  </div>
  <div class="card team-form">
    <div class="form-group">
      <Accordion title="Team Leader" opened={false}>
        <div class="agents-list">
          {#each agents as agent (agent.id)}
            {#if team.assigned_agents.includes(agent.id)}
              <RadioItem
                name="agents"
                value={agent.id}
                label={agent.name}
                onChange={handleTeamLeaderChange}
                checked={agent.id === team.leader_id}
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
    gap: 20px;
  }

  .team-form {
    display: flex;
    flex-direction: column;
    block-size: fit-content;
    gap: 10px;
    background-color: var(--bg-1);
    padding-inline: 20px;
    padding-block: 20px;
    border-radius: 5px;
    box-shadow: var(--shadow-sm);
    position: relative;
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

  .mode-switch {
    margin-bottom: 20px;
  }
</style>
