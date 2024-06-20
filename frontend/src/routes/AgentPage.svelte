<script lang="ts">
    import { getAgent, getLLMProviders, getTools } from "../common/utils";
    import type { AgentDetailInterface, ProviderInterface, ToolInterface } from "../common/interfaces";
    import Checkbox from "../lib/Checkbox.svelte";
    import Switch from "../lib/Switch.svelte";
  import LlmProvider from "../lib/LLMProvider.svelte";

    export let agent_id: String = '';
    let agent: AgentDetailInterface;
    let providers: ProviderInterface[] = [];
    let tools: ToolInterface[] = [];

    let toolsShown: boolean = false;
    let llmsShown: boolean = false;
    let selectedTools: string[] = [];
    
    const loadAgent = async(agent_id: String) => {
        try {
            agent = await getAgent(agent_id) as AgentDetailInterface;
        } catch (error) {
            console.log(error)
        }
    }

    (async () => {
        try {
            providers = await getLLMProviders() as ProviderInterface[];
            tools = await getTools() as ToolInterface[];
        } catch (error) {
            console.log(error)
        }
    })()

    const handleToolChange = (event: Event) => {
        const target = event.target as HTMLInputElement;
        if (target.checked) {
            if (!selectedTools.includes(target.value))
                selectedTools = [...selectedTools, target.value]
        } else {
            const index = selectedTools.indexOf(target.value);
            if (index > -1) {
                let oldArray = [...selectedTools]
                oldArray.splice(index, 1)
                selectedTools = [...oldArray]
            }
        }

        agent.tools = selectedTools
            .map(tool => tools.find(t => t.name === tool))
            .filter((tool): tool is ToolInterface => tool !== undefined);
    }
</script>

{#if agent_id}
    {#await loadAgent(agent_id) }
        <div class="container">
            <div class="loading">
                <i class="fas fa-spinner fa-spin fa-3x"></i>
            </div>
        </div>
    {:then}
    <div class="container ready">
        <div class="agent-form">
            <div class="form-group">
                <label for="name">Name of Agent</label>
                <input type="text" bind:value={agent.name} />
            </div>
            <div class="form-group">
                <label for="prompt">Agent Prompt</label>
                <textarea rows="15" bind:value={agent.prompt_template}></textarea>
            </div>
            <div class="form-group">
                <Switch label="Is Sub Agent" bind:checked={agent.is_sub_agent} />
            </div>
        </div>
        <div class="agent-form">
            <div class="form-group">
                <header>
                    <label for="tools">Choose LLM Provider</label>
                    <button on:click={() => llmsShown = !llmsShown} class="toggle-tools"> 
                        <i 
                            class={`fa-solid ${llmsShown ? 'fa-minus-square fa-fw' : 'fa-plus-square fa-fw'}`}
                        ></i>
                    </button>
                </header>
            </div>
            {#if llmsShown}
            <div class="form-group">
                <select bind:value={agent.llm}>
                {#each providers as provider, index (index)}
                    <option disabled></option>
                    {#if agent.llm.provider === provider.provider}
                        <option value={provider} selected>{provider.provider}</option>
                    {:else}
                        <option value={provider} >{provider.provider}</option>
                    {/if}
                {/each}
                </select>
            </div>
            <LlmProvider 
                bind:model={agent.llm.model}
                bind:api_key={agent.llm.api_key}
                bind:base_url={agent.llm.base_url}
                bind:temperature={agent.llm.temperature}
                bind:max_tokens={agent.llm.max_tokens}
                bind:is_multimodal={agent.llm.is_multimodal}
            />
            {/if}
        </div>
        <div class="agent-form">
            <div class="form-group">
                <header>
                    <label for="tools">Tools</label>
                    <button on:click={() => toolsShown = !toolsShown} class="toggle-tools"> 
                        <i 
                            class={`fa-solid ${toolsShown ? 'fa-minus-square fa-fw' : 'fa-plus-square fa-fw'}`}
                        ></i>
                    </button>
                </header>
            </div>
            {#if toolsShown}
            <div class="tools-list">
                {#each tools as tool, index (index)}
                    <Checkbox name="tools" value={tool.name} label={tool.name} onChange={handleToolChange}/>
                {/each}
            </div>
            {/if}
        </div>
    </div>
    {/await}
{/if}

<style>
    .container {
        width: 100%;
        height: 100%;
        padding: 20px;
        display: grid;
    }

    .container.ready {
        grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
        grid-auto-rows: minmax(100px, auto);
        gap: 20px;
        overflow-y: auto;
    }

    .agent-form {
        display: flex;
        flex-direction: column;
        height: fit-content;
        gap: 10px;
        background-color: var(--bg-1);
        padding: 20px;
        border-radius: 5px;
        box-shadow: var(--shadow-sm);
    }

    .tools-list {
        width: 100;
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        gap: 10px;
    }
</style>