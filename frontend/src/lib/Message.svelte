<script lang="ts">
  import { run } from 'svelte/legacy';

  import { marked } from "marked";
  import CodeBlock from "./CodeBlock.svelte";
  import AgentImg from "../assets/ai-agent-icon.svg";
  import { convertXmlToJson } from "../common/utils";
  import { onMount } from "svelte";
  import { fade, fly, slide } from 'svelte/transition';
    import Accordion from "./Accordion.svelte";

  interface Props {
    id?: string | number;
    role?: string;
    type?: string;
    content: string;
    image?: string;
    thought?: string | null;
    observation?: string | null;
    reflection?: string | null;
    artifacts?: object[];
  }

  let {
    id = "",
    role = "user",
    type = "text",
    content,
    image = "",
    thought = $bindable(null),
    observation = $bindable(null),
    reflection = $bindable(null),
    artifacts = $bindable([])
  }: Props = $props();

  let toolCalls: any[] = $state([]);
  let toolCallResults: object[] = [];
  let htmlContent: string | Promise<string> = $state("");

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
    if (artifacts && Array.isArray(artifacts)){
      let artifactsContent = "";
      
      for (let i = 0; i < artifacts.length; i++) {
        artifactsContent += formatOneArtifact(artifacts[i]) + "\n";
      }
  
      return artifactsContent;
    }

    return ''
  };

  const formatContent = (content: string): string => {
    if (role.toLowerCase() === 'user') {
      if (type === "text") {
        return content
      } else if (type === "code") {
        return "```code\n" + content + "\n```";
      }
    }

    
    let parsedContent = convertXmlToJson(content);
    if (role === 'CodeMaster'){
      // console.log(parsedContent)
    }
    
    if (!parsedContent) return content as string;

    for (let key in parsedContent) {
      
      if (key === "artifact") {
        let artifactsObjects = parsedContent[key];
        if (Array.isArray(artifactsObjects)) {
          artifacts = artifactsObjects;
        } else {
          artifacts = [artifactsObjects];
        }

      } else if (key === "tool_call") {
        let tool_calls = parsedContent[key];
        if (Array.isArray(tool_calls)) {
          toolCalls = tool_calls;
        } else {
          toolCalls = [tool_calls];
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
      } else if (key === "observation") {
        observation = "`" + parsedContent[key] + "`";
      } else if (key === "thought") {
        thought = "```code\n" + parsedContent[key] + "\n```";
      } else if (key === "reflection") {
        reflection = "```code\n" + parsedContent[key] + "\n```";
      }
    }

    const formatNode = (node: any): string => {
      if (typeof node === "string") return node;
      if (typeof node !== "object") return String(node);
      
      for (let key in node){
        if (['result', '#text'].includes(key)) {
          return `${formatNode(node[key])}\n\n`;
        }
      }

      return "";
    };

    return formatNode(parsedContent);
  };

  run(() => {
    htmlContent = role.toLowerCase() === "user" ? formatContent(content) : marked(formatContent(content));
  });
  let artifactsContent = $derived(marked(formatArtifacts(artifacts)));
</script>

<article
  class={`message ${role.toLowerCase() === "user" ? "user" : "astronaut"}`}
  id={`message${id}`}
>
  <div class="user-row">
    {#if role.toLowerCase() === "user"}
      <span class="user-name">{role}</span>
      <i class="fa-solid fa-user-circle fa-fw"></i>
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
    <div class="message-container">
      {#if observation}
        <Accordion title="Observation">
          <CodeBlock htmlContent={marked(observation)} />
        </Accordion>
      {/if}

      {#if thought}
        <Accordion title="Thought">
          <CodeBlock htmlContent={marked(thought)} />
        </Accordion>
      {/if}

      {#if reflection}
        <Accordion title="Reflection">
          <CodeBlock htmlContent={marked(reflection)} />
        </Accordion>
      {/if}
      {#each toolCalls as tool_call}
        <div class="tool-call">
          <i class="fas fa-anchor fa-fw"></i>
          <span>
            <em>Running Tool <b>{tool_call.name}</b></em> with parameters:
            <CodeBlock htmlContent={JSON.stringify(tool_call.arguments)} />
          </span>
        </div>
      {/each}
      {#if role.toLowerCase() === "user"}
        {htmlContent}
      {:else}
        <CodeBlock {htmlContent} />
      {/if}
      <CodeBlock htmlContent={artifactsContent} />
    </div>
    {#if image.length}
      <img src={image} alt="message" />
    {/if}
  </div>
</article>

<style>
  article {
    color: var(--fg-1);
    width: fit-content;
    min-width: 300px;
    max-width: 75%;
  }

  
  article.user {
    align-self: flex-end;
    /* text-align: end; */
  }

  .user-row {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-block-end: 5px;
  }

  article.user .user-row {
    justify-content: flex-end;
    padding-inline-end: 5px;
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
    background-color: var(--bg-1);
    border-radius: 15px;
    padding: 15px;
  }

  img.icon {
    width: 20px;
    height: 20px;
  }

  hr {
    border-color: var(--bg-2);
  }

  .message-container {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
</style>
