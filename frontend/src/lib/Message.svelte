<script lang="ts">
  import { marked } from "marked";
  import CodeBlock from "./CodeBlock.svelte";
  import AgentImg from "../assets/ai-agent-icon.svg";
  import { convertXmlToJson } from "../common/utils";
  import { onMount } from "svelte";

  export let id: string | number = "";
  export let role: string | String = "user";
  export let content: string;
  export let image: string = "";

  let artifacts: object[] = [];
  let toolCalls: object[] = [];
  let toolCallResults: object[] = [];
  let htmlContent: string | Promise<string> = "";

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

  const formatContent = (content: string): string => {
    let parsedContent = convertXmlToJson(content);

    if (!parsedContent) return content;

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

      if (node.type === "final_answer") {
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
  <div class="message-row">
    {#each toolCalls as tool_call}
      <div class="tool-call">
        <i class="fas fa-anchor fa-fw"></i>
        <span
          ><em>Running Tool <b>{tool_call?.name}</b></em> with parameters:
          <em>{JSON.stringify(tool_call?.arguments)}</em></span
        >
      </div>
    {/each}
    <CodeBlock {htmlContent} />
    <CodeBlock htmlContent={artifactsContent} />
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
</style>
