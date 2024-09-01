<script lang="ts">
  import { link, navigate } from "svelte-routing";
  import {
    getAgent,
    getLLMProviders,
    getTools,
    generatePrompt,
    saveAgent,
    getAgentSession,
  } from "../common/utils";
  import type {
    AgentDetailInterface,
    ProviderInterface,
    ToolInterface,
  } from "../common/interfaces";
  import GenerativeIcon from "../assets/ai-curved-star-icon-multiple.svg";
  import Checkbox from "../lib/Checkbox.svelte";
  import Switch from "../lib/Switch.svelte";
  import LlmProvider from "../lib/LLMProvider.svelte";
  import Accordion from "../lib/Accordion.svelte";
  import { webSocketStore } from "../common/stores";
  import type { Unsubscriber } from "svelte/motion";
  import { onDestroy, onMount } from "svelte";
  import Modal from "../lib/Modal.svelte";

  export let agent_id: string = "";
  let agent: AgentDetailInterface = {
    name: "",
    system_prompt: "",
    is_sub_agent: false,
    autostart: false,
    llm: {
      provider: "",
      model: "",
      api_key: "",
      temperature: 0.2,
      is_multimodal: false,
      supports_tool_use: false,
    },
  };
  let providers: ProviderInterface[] = [];
  let tools: ToolInterface[] = [];
  let agentTools: string[] = [];

  let toolsShown: boolean = false;
  let llmsShown: boolean = false;
  let selectedTools: string[] = [];
  let loading: boolean = false;
  let submitting: boolean = false;
  let unsubscribe: Unsubscriber | null = null;
  let socket: WebSocket;
  let newPromptTemplate: string = "";

  const loadAgent = async (agent_id: string) => {
    try {
      agent = (await getAgent(agent_id)) as AgentDetailInterface;
      if (agent.tools && agent.tools.length) {
        agentTools = agent.tools.map((t) => t.name);
      }
      console.log(agent);
    } catch (error) {
      console.log(error);
    }
  };

  const generateAgentPrompt = () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      webSocketStore.send(
        JSON.stringify({
          type: "generate",
          action: "system_prompt",
          name: agent.name,
          prompt: agent.system_prompt,
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
          console.log(socket);
          if (event.type === "message") {
            loading = false;
            let data = JSON.parse(event.data);

            if (data.type == "generate") {
              if (!newPromptTemplate.length) {
                agent.system_prompt = "";
              }
              newPromptTemplate = data.content;
              agent.system_prompt = agent.system_prompt + data.content;
              loading = false;
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
      providers = (await getLLMProviders()) as ProviderInterface[];
      tools = (await getTools()) as ToolInterface[];
    } catch (error) {
      console.log(error);
    }
  })();

  const handleToolChange = (event: Event) => {
    const target = event.target as HTMLInputElement;
    if (target.checked) {
      if (!selectedTools.includes(target.value))
        selectedTools = [...selectedTools, target.value];
    } else {
      const index = selectedTools.indexOf(target.value);
      if (index > -1) {
        let oldArray = [...selectedTools];
        oldArray.splice(index, 1);
        selectedTools = [...oldArray];
      }
    }

    agent.tools = selectedTools
      .map((tool) => tools.find((t) => t.name === tool))
      .filter((tool): tool is ToolInterface => tool !== undefined);
  };

  const handleAgentSubmit = async () => {
    try {
      submitting = true;
      agent = (await saveAgent(agent)) as AgentDetailInterface;
      if (agent.id) agent_id = agent.id;
    } catch (error) {
      console.log(error);
    } finally {
      submitting = false;
    }
  };

  const chatWithAgent = async () => {
    try {
      const { session_id } = (await getAgentSession(agent_id)) as {
        session_id: string;
      };
      if (session_id) navigate(`/${session_id}`, { replace: true });
    } catch (error) {
      console.log(error);
    }
  };

  const handleLLMChange = (event: Event) => {
    const target = event.target as HTMLInputElement;
    const selectedProvider =
      providers.find((provider) => provider.provider === target.value) || {};
    if (Object.keys(selectedProvider).length) {
      agent.llm = { ...agent.llm, ...selectedProvider };
    }
  };

  onDestroy(() => {
    if (unsubscribe) unsubscribe();
  });

  // $: if (newPromptTemplate) {
  //   agent.system_prompt = agent.system_prompt + newPromptTemplate;
  // }
</script>

<Modal
  isOpen={true}
  type="confirm"
  action={() => console.log("Confirmed!")}
  actionLabel="Yes, do it"
  size="small"
  appearance="bordered"
>
  Are you sure you want to proceed?
</Modal>
{#if agent_id}
  {#await loadAgent(agent_id)}
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
        <span>Assign Task</span>
      </button>
      <button on:click={chatWithAgent} class="btn">
        <i class="fa-regular fa-comments fa-fw"></i>
        <span>Chat</span>
      </button>
    </div>
  {/if}
  <button class="btn" disabled={submitting} on:click={handleAgentSubmit}>
    <i class="fa-solid fa-save fa-fw"></i>
    <span
      >{submitting
        ? "Saving..."
        : agent_id
          ? "Update Agent"
          : "Save Agent"}</span
    >
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
      <textarea
        bind:value={agent.system_prompt}
        placeholder="Provide a brief description of your agent and click the Generate Prompt button to generate a system prompt for your Agent."
      ></textarea>
    </div>
    <button
      class="btn ai-generate"
      on:click={generateAgentPrompt}
      disabled={agent.system_prompt === "" || loading}
    >
      <img src={GenerativeIcon} alt="generative" class="icon" />
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
        <select
          bind:value={agent.llm.provider}
          on:change={(e) => {
            handleLLMChange(e);
          }}
        >
          {#each providers as provider, index (index)}
            <option disabled></option>
            <option value={provider.provider} selected
              >{provider.provider}</option
            >
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
  <div class="agent-form creativity">
    <label for="temperature">Creativity (Temperature)</label>
    <div class="range-container">
      <input
        type="range"
        id="temperature"
        name="temperature"
        min="0"
        max="1"
        step="0.1"
        bind:value={agent.llm.temperature}
      />
      <span>{agent.llm.temperature.toFixed(1)}</span>
    </div>
  </div>
  <div class="agent-form tools">
    <Accordion title="Tools">
      <div class="tools-list">
        {#each tools as tool, index (index)}
          <Checkbox
            name="tools"
            value={tool.name}
            label={tool.name}
            onChange={handleToolChange}
            checked={agentTools.includes(tool.name)}
          />
        {/each}
      </div>
    </Accordion>
  </div>
</div>

<style>
  .container {
    inline-size: 700px;
    max-inline-size: 100%;
    padding-inline: 20px;
    padding-block: 20px;
    display: grid;
    margin-inline: auto;
    margin-block: 0;
  }

  .container.ready {
    gap: 20px;
    overflow-y: auto;
  }

  .toolbar {
    position: sticky;
    inset-inline: 0;
    inset-block: 0;
    background-color: inherit;
  }

  .agent-form {
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

  .btn.ai-generate {
    margin-inline-start: auto;
    inline-size: fit-content;
    background-color: var(--fg-1);
    color: var(--bg-1);
    padding-inline: 7px;
    padding-block: 5px;
    border-radius: 7px;
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .tools {
    z-index: 10;
  }

  .tools-list {
    inline-size: 100%;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
  }

  textarea {
    resize: none;
    block-size: 30vh;
  }

  img.icon {
    inline-size: 30px;
    block-size: 30px;
  }

  .creativity {
    z-index: 10;
    text-align: start;
  }

  .range-container {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  input[type="range"] {
    flex-grow: 1;
  }

  .range-container span {
    min-width: 30px;
    text-align: right;
  }
</style>
