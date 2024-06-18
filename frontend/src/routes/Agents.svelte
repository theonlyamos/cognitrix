<script lang="ts">
    import { getAllAgents } from "../common/utils";
    import type { AgentInterface } from "../common/interfaces";
    
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
<div class="agents-container">
    {#each agents as agent (agent.id)}
        <div class="agent-card">
            <div class="agent-detail">
                <div class="agent-detail-key">
                    Name:
                </div>
                <div class="agent-detail-value">
                    {agent.name}
                </div>
            </div>
            <div class="agent-detail">
                <div class="agent-detail-key">
                    Model:
                </div>
                <div class="agent-detail-value">
                    {agent.model}
                </div>
            </div>
            <div class="agent-detail">
                <div class="agent-detail-key">
                    Provider:
                </div>
                <div class="agent-detail-value">
                    {agent.provider}
                </div>
            </div>
            <div class="agent-detail">
                <div class="agent-detail-key">
                    Tools:
                </div>
                <div class="agent-detail-value">
                    {agent.tools}
                </div>
            </div>
        </div>
    {/each}
</div>
{/await}

<style>
    .loading {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        height: 100%;
    }

    .fa-spinner {
        color: var(--bg-2);
    }

    .agents-container {
        padding: 20px;
        display: flex;
        gap: 20px;
    }

    .agent-card {
        text-align: start;
        padding: 10px;
        border-radius: 4px;
        background-color: var(--fg-1);
        color: var(--bg-2);
        min-width: 250px;
    }

    .agent-detail {
        width: 100%;
        text-align: start;
        display: flex;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 5px;
    }

</style>
