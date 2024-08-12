<script lang="ts">
  import { marked } from "marked";
  import CodeBlock from "./CodeBlock.svelte";
  import AgentImg from "../assets/ai-agent-icon.svg";

  export let id: string | number = "";
  export let role: string | String = "user";
  export let content: string;
  export let image: string = "";
  export let artifacts: object | object[] = {};

  const formatOneArtifact = (artifact: any) => {
    let content = "";
    if (Object.keys(artifact).length) {
      content = artifact.content;
      if (
        Object.keys(artifact).includes("language") &&
        artifact.language &&
        typeof artifact.language === "string"
      ) {
        content = "```" + artifact.language + "\n" + content + "\n```";
      }
    }

    return content;
  };
  const formatArtifacts = (artifacts: any | object[]) => {
    let content = "";
    if (typeof artifacts === "object") {
      content = formatOneArtifact(artifacts);
    } else if (Array.isArray(artifacts)) {
      for (let i = 0; i < artifacts.length; i++) {
        content += formatOneArtifact(artifacts[i]) + "\n";
      }
    }
    return content;
  };

  const formatContent = (content: any): string => {
    if (typeof content === "object") {
      let new_content: string = "";
      for (let k in content) {
        new_content += `## ${k.charAt(0).toUpperCase()}${k.slice(1)}\n\n${content[k]}\n\n`;
      }
      content = new_content;
    }

    return content;
  };

  // $: console.log(content);
  $: htmlContent = marked(formatContent(content));
  $: artifactsContent = marked(formatArtifacts(artifacts));
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
    <CodeBlock {htmlContent} />
    <!-- <CodeBlock htmlContent={artifactsContent} /> -->
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
