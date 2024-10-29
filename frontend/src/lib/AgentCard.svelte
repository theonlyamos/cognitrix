<script lang="ts">
  import { link } from "svelte-routing";
  import type { AgentDetailInterface } from "../common/interfaces";
  import { onMount } from "svelte";

  export let agent: AgentDetailInterface;
  let agentModel: string[] | null = null;
  let model: string = "";

  onMount(() => {
    agentModel = agent?.llm?.model?.split("/");
  });

  $: if (agentModel) model = agentModel[agentModel.length - 1];

  function handleEdit() {
    // TODO: Implement edit functionality
    console.log(`Edit agent ${agent.id}`);
  }

  function handleDelete() {
    // TODO: Implement delete functionality
    console.log(`Delete agent ${agent.id}`);
  }

  function handleRun() {
    // TODO: Implement run functionality
    console.log(`Run agent ${agent.id}`);
  }

  function handleViewHistory() {
    // TODO: Implement view history functionality
    console.log(`View history for agent ${agent.id}`);
  }

  // Add these functions if they don't exist in your AgentInterface
  function getShortDescription(system_prompt: string, maxLength: number = 50) {
    return system_prompt.length > maxLength 
      ? system_prompt.slice(0, maxLength) + '...' 
      : system_prompt;
  }

  function formatDate(dateString: string) {
    return new Date(dateString).toLocaleDateString();
  }
</script>

<div class="card agent-card" id={`agent-${agent.id}`} data-id={agent.id}>
  <a href={`/agents/${agent.id}`} use:link class="agent-info">
    <h3 class="agent-name">{agent.name}</h3>
    {#if agent.system_prompt}
      <p class="agent-system_prompt">
        {getShortDescription(agent.system_prompt)}
      </p>
    {/if}
    <div class="agent-details">
      <div class="agent-detail">
        <i class="fas fa-robot"></i>
        <span>{model}</span>
      </div>
      <div class="agent-detail">
        <i class="fas fa-server"></i>
        <span>{agent.llm.provider}</span>
      </div>
      <div class="agent-detail">
        <i class="fas fa-tools"></i>
        <span
          >{agent?.tools
            ?.map((tool) => tool.name)
            .slice(0, 3)
            .join(", ")}...</span
        >
      </div>
      {#if agent.created_at}
        <div class="agent-detail">
          <i class="fas fa-calendar-alt"></i>
          <span>Created: {formatDate(agent.created_at)}</span>
        </div>
      {/if}
    </div>
  </a>
  <div class="agent-options">
    <button on:click={handleEdit} class="option-button edit">
      <i class="fas fa-edit"></i> Edit
    </button>
    <button on:click={handleDelete} class="option-button delete">
      <i class="fas fa-trash-alt"></i> Delete
    </button>
    <button on:click={handleRun} class="option-button run">
      <i class="fa-solid fa-comments"></i> Chat
    </button>
    <button on:click={handleViewHistory} class="option-button history">
      <i class="fas fa-history"></i> History
    </button>
  </div>
</div>

<style>
  .agent-card {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 20px;
    border-radius: 12px;
    background: var(--bg-1);
    color: var(--fg-1);
    min-inline-size: 300px;
    max-inline-size: 100vmin;
    block-size: auto;
    box-shadow: 0 10px 20px rgba(0, 0, 0, 0.1);
    transition: all 0.3s ease;
    overflow: hidden;
  }

  .agent-card:hover {
    transform: translateY(-5px);
    box-shadow: 0 15px 30px rgba(0, 0, 0, 0.15);
  }

  .agent-info {
    display: flex;
    flex-direction: column;
    gap: 15px;
    margin-bottom: 20px;
    text-decoration: none;
    color: inherit;
  }

  .agent-name {
    font-size: 1.5em;
    font-weight: 600;
    margin: 0;
    color: var(--accent-color, #2196F3);
  }

  .agent-details {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .agent-detail {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.9em;
  }

  .agent-detail i {
    width: 20px;
    text-align: center;
    color: var(--accent-color, #2196F3);
  }

  .agent-options {
    display: flex;
    justify-content: space-between;
    gap: 10px;
  }

  .option-button {
    flex: 1;
    padding: 8px 12px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 0.9em;
    transition: all 0.3s ease;
    display: flex;
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
  .run { background-color: #2196F3; color: white; }
  .history { background-color: #FF9800; color: white; }

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

  .agent-system_prompt {
    font-size: 0.9em;
    color: var(--fg-2);
    margin: 0;
  }
</style>
