<script lang="ts">
  import { marked } from "marked";
  import CodeBlock from "./CodeBlock.svelte";
  import AgentImg from "../assets/ai-agent-icon.svg";
  import { convertXmlToJson } from "../common/utils";
  import { onMount } from "svelte";
  import { fade, fly, slide } from 'svelte/transition';

  export let id: string | number = "";
  export let role: string | String = "user";
  export let content: string|object;
  export let image: string = "";
  export let thought: string | null = null;
  export let observation: string | null = null;
  export let reflection: string | null = null;

  let artifacts: object[] = [];
  let toolCalls: any[] = [];
  let toolCallResults: object[] = [];
  let htmlContent: string | Promise<string> = "";

  let showThought = false;
  let showObservation = false;
  let showReflection = false;

  const formatOneArtifact = (artifact: any) => {
    let artifactContent = "";
    if (Object.keys(artifact).length) {
      artifactContent = artifact.content;
      if (
        Object.keys(artifact).includes("language") &&
        artifact.language &&
        typeof artifact.language === "string"
      ) {
        artifactContent =
          "```" + artifact.language + "\n" + artifactContent + "\n```";
      }
    }

    return artifactContent;
  };

  const formatArtifacts = (artifacts: object[]) => {
    let artifactsContent = "";

    for (let i = 0; i < artifacts.length; i++) {
      artifactsContent += formatOneArtifact(artifacts[i]) + "\n";
    }

    return artifactsContent;
  };

  const formatContent = (content: string|object): string => {
    if (typeof content === 'object') {
      content = content.llm_response as string;
    }
    let parsedContent = convertXmlToJson(content as string);

    if (!parsedContent) return content as string;

    for (let key in parsedContent) {
      if (key === "artifacts") {
        let artifactsObjects = parsedContent[key];

        if (Object.keys(artifactsObjects).length) {
          if (Array.isArray(artifactsObjects.artifact)) {
            artifacts = artifactsObjects.artifact;
          } else {
            artifacts = [artifactsObjects.artifact];
          }
        }
      } else if (key === "tool_calls") {
        let tool_calls = parsedContent[key];
        if (Object.keys(tool_calls).length) {
          if (Array.isArray(tool_calls.tool)) {
            toolCalls = tool_calls.tool;
          } else {
            toolCalls = [tool_calls.tool];
          }
        }
      } else if (key === "tool_call_results") {
        let tool_call_results = [];
        if (Object.keys(parsedContent[key]).length) {
          if (Array.isArray(parsedContent[key].tool)) {
            tool_call_results = parsedContent[key].tool;
          } else {
            tool_call_results = [parsedContent[key].tool];
          }
        }

        for (let i = 0; i < tool_call_results.length; i++) {
          console.log(toolCalls);
          // console.log(toolCalls[i]);
          // toolCalls[i]["result"] = tool_call_results[i];
        }
      }
    }

    const formatNode = (node: any): string => {
      if (typeof node === "string") return node;
      if (typeof node !== "object") return String(node);

      if (node.type === "result") {
        return `${formatNode(node.result)}\n\n`;
      }

      return "";

      // return Object.entries(node)
      //   .map(([key, value]) => {
      //     if (key === "#text") return String(value);

      //     if (key === "result") {
      //       return `${formatNode(value)}\n\n`;
      //     }
      //   })
      //   .join("");
    };

    return formatNode(parsedContent);
  };

  $: htmlContent = marked(formatContent(content));
  $: artifactsContent = marked(formatArtifacts(artifacts));

  $: console.log(content);
</script>

<article
  class={`message ${role === "user" ? "user" : "astronaut"}`}
  id={`message${id}`}
>
  <div class="user-row">
    {#if role === "user"}
      <span class="user-name">{role}</span>
      <i class="fas fa-user fa-fw"></i>
    {:else}
      <img src={AgentImg} class="icon" alt="agent" />
      <span class="user-name">{role}</span>
    {/if}
  </div>
  <hr />
  <div
    class="message-row"
    in:fly={{ y: 20, duration: 300 }}
    out:fade={{ duration: 200 }}
  >
    {#each toolCalls as tool_call}
      <div class="tool-call">
        <i class="fas fa-anchor fa-fw"></i>
        <span
          ><em>Running Tool <b>{tool_call.name}</b></em> with parameters:
          <em>{JSON.stringify(tool_call.arguments)}</em></span
        >
      </div>
    {/each}
    <CodeBlock {htmlContent} />
    <CodeBlock htmlContent={artifactsContent} />

    {#if thought}
      <div class="toggle-section">
        <button on:click={() => (showThought = !showThought)}>
          {showThought ? "Hide" : "Show"} Thought
        </button>
        {#if showThought}
          <div transition:slide>
            <h4>Thought:</h4>
            <CodeBlock htmlContent={marked(thought)} />
          </div>
        {/if}
      </div>
    {/if}

    {#if observation}
      <div class="toggle-section">
        <button on:click={() => (showObservation = !showObservation)}>
          {showObservation ? "Hide" : "Show"} Observation
        </button>
        {#if showObservation}
          <div transition:slide>
            <h4>Observation:</h4>
            <CodeBlock htmlContent={marked(observation)} />
          </div>
        {/if}
      </div>
    {/if}

    {#if reflection}
      <div class="toggle-section">
        <button on:click={() => (showReflection = !showReflection)}>
          {showReflection ? "Hide" : "Show"} Reflection
        </button>
        {#if showReflection}
          <div transition:slide>
            <h4>Reflection:</h4>
            <CodeBlock htmlContent={marked(reflection)} />
          </div>
        {/if}
      </div>
    {/if}
  </div>
  {#if image.length}
    <img src={image} alt="message" />
  {/if}
</article>

<style>
  article {
    background-color: var(--bg-1);
    width: fit-content;
    min-width: 300px;
    max-width: 75%;
    border-radius: 15px;
    padding: 20px;
    color: var(--fg-1);
  }

  .user-row {
    display: flex;
    align-items: center;
    gap: 5px;
  }

  article.user {
    align-self: flex-end;
    /* text-align: end; */
  }

  i {
    font-size: 1.2rem;
  }

  .user-name {
    text-transform: capitalize;
  }

  .message-row {
    overflow-wrap: break-word;
    text-align: start;
  }

  img.icon {
    width: 20px;
    height: 20px;
  }

  hr {
    border-color: var(--bg-2);
  }

  .toggle-section {
    margin-top: 10px;
  }

  .toggle-section button {
    background-color: var(--bg-2);
    color: var(--fg-1);
    border: none;
    padding: 5px 10px;
    border-radius: 5px;
    cursor: pointer;
  }

  .toggle-section h4 {
    margin-top: 10px;
    margin-bottom: 5px;
  }
</style>
