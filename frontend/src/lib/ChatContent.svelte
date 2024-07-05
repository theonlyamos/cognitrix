<script lang="ts">
    import { afterUpdate, beforeUpdate } from "svelte"; 
    import type { MessageInterface, SessionInterface } from "../common/interfaces";
    import  MessageComponent from "./Message.svelte";
    import { link } from "svelte-routing";

    export let sessions: SessionInterface[] = [];
    export let messages: MessageInterface[];
    let container: HTMLElement;

    let autoscroll = false;

	beforeUpdate(() => {
		if (container) {
			const scrollableDistance = container.scrollHeight - container.offsetHeight;
			autoscroll = container.scrollTop > scrollableDistance - 20;
		}
	});

	afterUpdate(() => {
		if (autoscroll) {
			container.scrollTo(0, container.scrollHeight);
		}
	});
</script>

<div class="container">
    <div class="chat-sessions">
        <h3>Chat Sessions</h3>
        {#each sessions as session (session.id)}
            <a href="/{session.id}" use:link class="session-item">
                {session.datetime}
            </a>
        {/each}
    </div>
    <div class="main-chat-container">
        <div class="chat-content" bind:this={container}>
            {#each messages as message, index (index)}
                <MessageComponent {...message}/>
            {/each}
        </div>
        <slot/>
    </div>
</div>

<style>
    .container {
        width: 100%;
        height: 100%;
        display: flex;
        justify-content: space-between;
        gap: 10px;
    }
    .chat-sessions {
        width: 15%;
        height: 100%;
        color: var(--bg-1);
        display: flex;
        gap: 10px;
        flex-direction: column;
        box-sizing: border-box;
        overflow-y: auto;
        text-align: start;
        border-right: 1px solid var(--bg-1);
        color: var(--fg-1);
    }

    h3 {
        margin-bottom: 0;
        border-bottom: 1px solid var(--fg-2);
        padding: 0 10px 10px 10px;
    }

    .session-item {
        color: var(--fg-1);
        font-size: 0.8rem;
        padding: 0 10px 10px 10px;

        &:hover, &:focus, &:active {
            color: var(--fg-2);
        }
    }

    .main-chat-container {
        width: 83%;
        height: 100%;
    }

    .chat-content {
        width: 100%;
        height: 85%;
        color: var(--bg-1);
        border-radius: 7px;
        display: flex;
        gap: 20px;
        flex-direction: column;
        box-sizing: border-box;
        overflow-y: auto;
        text-align: start;
        margin-top: 20px;
        padding-bottom: 20px;
        padding-right: 20px;
    }
</style>
