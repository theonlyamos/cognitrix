<script lang="ts">
    import { getAllAgents } from "../common/utils";
    import type { AgentInterface } from "../common/interfaces";
  import AgentCard from "../lib/AgentCard.svelte";
  import { link } from "svelte-routing";
    
    let agents: AgentInterface[] = [];

    const loadAgents = async()=> {
        try {
            agents = await getAllAgents() as AgentInterface[];
        } catch (error) {
            console.log(error)
        }
    }

</script>

{#await loadAgents()}
    <div class="loading">
        <i class="fas fa-spinner fa-spin fa-3x"></i>
    </div>
{:then}
<div class="container">
    <div class="toolbar">
        <a href="/agents/new" use:link class="btn">
            <i class="fa-solid fa-robot fa-fw"></i>
            <span>New Agent</span>
        </a>
    </div>
    <div class="agents-container">
        {#each agents as agent (agent.id)}
            <AgentCard agent={agent} />
        {/each}
    </div>
</div>
{/await}

<style>

    .container {
        padding: 20px;
    }

    .toolbar {
        display: flex;
        justify-content: end;
        gap: 10px;
    }

    .btn {
        padding: 10px;
        border-radius: 5px;
        background-color: var(--bg-1);
        box-shadow: var(--shadow-sm);
        color: var(--fg-2);
        
        &:hover {
            color: var(--fg-1);
        }
    }

    .agents-container {
        padding: 20px;
        display: flex;
        flex-wrap: wrap;
        gap: 20px;
    }

</style>
