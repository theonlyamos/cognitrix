<script lang="ts">
    import { getAllAgents } from "../common/utils";
    import type { AgentInterface } from "../common/interfaces";
    import AgentCard from "../lib/AgentCard.svelte";
    import { link } from "svelte-routing";
    
    let agents: AgentInterface[] = [];

    const loadAgents = async()=> {
        try {
            agents = await getAllAgents() as AgentInterface[];
            console.log(agents)
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
<div class="toolbar">
    <a href="/agents/new" use:link class="btn">
        <i class="fa-solid fa-robot fa-fw"></i>
        <span>New Agent</span>
    </a>
</div>
<div class="container">
    <div class="agents-container">
        {#each agents as agent (agent.id)}
            <AgentCard agent={agent} />
        {/each}
    </div>
</div>
{/await}

<style>
    .agents-container {
        padding: 20px;
        display: flex;
        flex-wrap: wrap;
        gap: 20px;
    }

</style>
