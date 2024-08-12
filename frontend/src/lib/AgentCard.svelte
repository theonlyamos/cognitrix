<script lang="ts">
  import { link } from "svelte-routing";
  import type { AgentInterface } from "../common/interfaces";
  import { onMount } from "svelte";

  export let agent: AgentInterface;
  let agentModel: string[] | null = null;
  let model: string = "";

  onMount(() => {
    agentModel = agent.model.split("/");
  });

  $: if (agentModel) model = agentModel[agentModel.length - 1];
</script>

<a
  href={`/agents/${agent.id}`}
  use:link
  class="agent-card"
  id={`agent-${agent.id}`}
  data-id={agent.id}
>
  <div class="agent-detail">
    <div class="agent-detail-key">Name:</div>
    <div class="agent-detail-value">
      {agent.name}
    </div>
  </div>
  <div class="agent-detail">
    <div class="agent-detail-key">Model:</div>
    <div class="agent-detail-value">
      {model}
    </div>
  </div>
  <div class="agent-detail">
    <div class="agent-detail-key">Provider:</div>
    <div class="agent-detail-value">
      {agent.provider}
    </div>
  </div>
  <div class="agent-detail">
    <div class="agent-detail-key">Tools:</div>
    <div class="agent-detail-value">
      {agent?.tools?.slice(0, 2).join(", ")}...
    </div>
  </div>
</a>

<style>
  .agent-card {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    text-align: start;
    padding: 10px;
    border-radius: 10px;
    background: linear-gradient(to bottom, var(--bg-1), var(--bg-1));
    color: var(--fg-1);
    min-inline-size: 250px;
    max-inline-size: 100vmin;
    block-size: 20vmin;
    box-shadow:
      0 10px 20px rgba(0, 0, 0, 0.3),
      0 6px 6px rgba(0, 0, 0, 0.2),
      inset 0 1px 1px rgba(255, 255, 255, 0.1);
    position: relative;
    overflow: hidden;

    &:hover {
      box-shadow:
        0 5px 10px rgba(0, 0, 0, 0.3),
        0 6px 6px rgba(0, 0, 0, 0.2),
        inset 0 1px 1px rgba(255, 255, 255, 0.1);
    }
  }

  .agent-card::before {
    content: "";
    position: absolute;
    inset-block-start: 0;
    inset-inline-start: 0;
    inline-size: 200%;
    block-size: 200%;
    background: radial-gradient(circle, var(--bg-2) 0%, var(--bg-1) 60%);
    opacity: 0.3;
    border-radius: 10px;
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
