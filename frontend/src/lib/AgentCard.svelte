<script lang="ts">
  import { link } from "svelte-routing";
  import type { AgentInterface } from "../common/interfaces";
  import { onMount } from "svelte";

    export let agent: AgentInterface;
    let agentModel: string[] | null = null
    let model: string = '';

    onMount(()=>{
        agentModel = agent.model.split('/')
    }) 

    $: if (agentModel) model = agentModel[agentModel.length - 1]
</script>
  
<a href={`/agents/${agent.id}`} 
    use:link class="agent-card" 
    id={`agent-${agent.id}`} 
    data-id={agent.id}>
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
            {model}
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
</a>
 
<style>
    .agent-card {
        text-align: start;
        padding: 10px;
        border-radius: 4px;
        background-color: var(--bg-1);
        color: var(--fg-2);
        min-width: 250px;
        
        &:hover {
            box-shadow: var(--shadow-sm);
            color: var(--fg-1);
        }
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