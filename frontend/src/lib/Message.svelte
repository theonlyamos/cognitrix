<script lang="ts">
    import { marked } from "marked";
    import CodeBlock from './CodeBlock.svelte';
    import AgentImg from '../assets/ai-agent-icon.svg';

    export let id: string|number = "";
    export let role: string|String = "user";
    export let content: string;
    export let image: string = "";

    let htmlContent = marked(content);
</script>

<article class={`message ${role === 'user' ? 'user' : 'astronaut'}`} id={`message${id}`}>
    <div class="user-row">
        {#if role === 'user'}
        <span class="user-name">{role}</span>
        <i class="fas fa-user fa-fw"></i>
        {:else}
        <img src={AgentImg} class="icon" alt="agent" />
        <span class="user-name">{role}</span>
        {/if}
        
    </div>
    <hr>
    <div class="message-row">
        <CodeBlock htmlContent={htmlContent}/>
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
        text-align: end;
    }

    i {
        font-size: 1.2rem;
    }

    .user-name {
        text-transform: capitalize;
    }

    .message-row {
        overflow-wrap: break-word;
    }

    img.icon {
        width: 20px;
        height: 20px;
    }
</style>