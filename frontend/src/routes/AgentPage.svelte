<script lang="ts">
    import { link, navigate } from "svelte-routing";
    import { getAgent, getLLMProviders, getTools, generatePrompt, saveAgent, getAgentSession } from "../common/utils";
    import type { AgentDetailInterface, ProviderInterface, ToolInterface } from "../common/interfaces";
    import GenerativeIcon from '../assets/ai-curved-star-icon-multiple.svg';
    import Checkbox from "../lib/Checkbox.svelte";
    import Switch from "../lib/Switch.svelte";
    import LlmProvider from "../lib/LLMProvider.svelte";
  import Accordion from "../lib/Accordion.svelte";

    export let agent_id: string = '';
    let agent: AgentDetailInterface = {
        name: '',
        prompt_template: '',
        is_sub_agent: false,
        autostart: false,
        llm: {
            provider: '',
            model: '',
            api_key: '',
            temperature: 0.2,
            is_multimodal: false,
            supports_tool_use: false,
        }
    };
    let providers: ProviderInterface[] = [];
    let tools: ToolInterface[] = [];

    let toolsShown: boolean = false;
    let llmsShown: boolean = false;
    let selectedTools: string[] = [];
    let loading: boolean = false;
    let submitting: boolean = false;
    
    const loadAgent = async(agent_id: string) => {
        try {
            agent = await getAgent(agent_id) as AgentDetailInterface;
        } catch (error) {
            console.log(error)
        }
    }

    const generateAgentPrompt = async() => {
        try {
            loading = true;
            const response = await generatePrompt(agent.name, agent.prompt_template) as {status: boolean, data: string};
            agent.prompt_template = response.data
        } catch (error) {
            console.log(error)
        } finally {
            loading = false;
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

    const handleAgentSubmit = async() => {
        try {
            submitting = true;
            agent = await saveAgent(agent) as AgentDetailInterface;
            if (agent.id)
                agent_id = agent.id
        } catch (error) {
            console.log(error)
        } finally {
            submitting = false;
        }
    }

    const chatWithAgent = async() => {
        try {
            const {session_id} = await getAgentSession(agent_id) as {session_id: string};
            if (session_id)
                navigate(`/${session_id}`, {replace: true});
        } catch (error) {
            console.log(error)
        }
    }

    const handleLLMChange = (event: Event) => {
        const target = event.target as HTMLInputElement;
        const selectedProvider = providers.find(provider => provider.provider === target.value) || {};
        if (Object.keys(selectedProvider).length){
            agent.llm = {...agent.llm, ...selectedProvider};
        }
    }
</script>

{#if agent_id}
    {#await loadAgent(agent_id) }
        <div class="container">
            <div class="loading">
                <i class="fas fa-spinner fa-spin fa-3x"></i>
            </div>
        </div>
    {/await}
{/if}
<div class="toolbar">
    {#if agent_id}
    <div style="margin-right: auto; display: flex; gap: 10px;">
        <button class="btn">
            <i class="fa-solid fa-tools fa-fw"></i>
            <span>Task</span>
        </button>
        <button on:click={chatWithAgent} class="btn">
            <i class="fa-regular fa-comments fa-fw"></i>
            <span>Chat</span>
        </button>
    </div>
    {/if}
    <button 
        class="btn" 
        disabled={submitting}
        on:click={handleAgentSubmit}>
        <i class="fa-solid fa-robot fa-fw"></i>
        <span>{submitting ? 'Saving...' : agent_id ? 'Update Agent' : 'Save Agent'}</span>
    </button>
</div>
<div class="container ready">
    <div class="agent-form">
        <div class="form-group">
            <label for="name">Name of Agent</label>
            <input type="text" bind:value={agent.name} />
        </div>
        <!-- <div class="form-group">
            <Switch label="Is Sub Agent" bind:checked={agent.is_sub_agent} />
        </div> -->
    </div>
    <div class="agent-form">
        <div class="form-group">
            <label for="prompt">Agent Prompt</label>
            <textarea rows="15" bind:value={agent.prompt_template} placeholder="Provide a brief description of your agent and click the Generate Prompt button to generate a system prompt for your Agent."></textarea>
        </div>
        <button 
            class="btn ai-generate" 
            on:click={generateAgentPrompt}
            disabled={agent.prompt_template === '' || loading}
        >
            <img src={GenerativeIcon} alt="generative" class="icon">
            {#if loading}
                <i class="fas fa-spinner fa-spin"></i>
            {:else}
                <span>Generate Prompt</span>
            {/if}
        </button>
    </div>
    <div class="agent-form">
        <Accordion title="Choose LLM Provider">
            <div class="form-group">
                <select bind:value={agent.llm.provider} on:change={(e)=>{handleLLMChange(e)}}>
                {#each providers as provider, index (index)}
                    <option disabled></option>
                    <option value={provider.provider} selected>{provider.provider}</option>
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
        </Accordion>
    </div>
    <div class="agent-form tools">
        <Accordion title="Tools">
            <div class="tools-list">
                {#each tools as tool, index (index)}
                    <Checkbox name="tools" value={tool.name} label={tool.name} onChange={handleToolChange}/>
                {/each}
            </div>
        </Accordion>
    </div>
</div>

<style>
    .container {
        width: 700px;
        max-width: 100%;
        padding: 20px;
        display: grid;
        margin: 0 auto;
    }

    .container.ready {
        gap: 20px;
        overflow-y: auto;
    }

    .toolbar {
        position: sticky;
        top: 0;
        left: 0;
        background-color: inherit;
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
        position: relative;
    }

    .btn.ai-generate {
        width: fit-content;
        position: absolute;
        bottom: 30px;
        right: 40px;
        background-color: var(--fg-1);
        color: var(--bg-1);
        padding: 5px;
        border-radius: 7px;
        display: flex;
        align-items: center;
        gap: 2px;
    }

    .tools {
        z-index: 10;
    }

    .tools-list {
        width: 100;
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
        gap: 10px;
    }

    textarea {
        resize: none;
    }

    img.icon {
        width: 30px;
        height: 30px;
    }
</style>