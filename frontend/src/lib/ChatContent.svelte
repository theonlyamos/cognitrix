<script lang="ts">
    import { afterUpdate, beforeUpdate } from "svelte"; 
    import type { MessageInterface, SessionInterface } from "../common/interfaces";
    import  MessageComponent from "./Message.svelte";
    import { link } from "svelte-routing";
    import type { MouseEventHandler } from "svelte/elements";

    export let sessions: SessionInterface[] = [];
    export let messages: MessageInterface[];
    export let clearMessages: MouseEventHandler<HTMLElement> = ()=>{};

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

    $: if (messages.length){
        if (autoscroll) {
			container.scrollTo(0, container.scrollHeight);
		}
    }
</script>

<div class="container">
    <div class="chat-sessions">
        <h3>Chat Sessions</h3>
        {#each sessions as session (session.id)}
            <a href="/{session.id}" use:link class="session-item">
                <span>{session.datetime}</span>
                <button>
                    <i class="fa-solid fa-xmark"></i>
                </button>
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
    <button class="clear-btn" on:click={clearMessages}>
        <i class="fa-solid fa-comment-slash fa-fw"></i>Clear
    </button>
</div>

<style>
    .container {
        width: 100%;
        height: 100%;
        display: flex;
        justify-content: space-between;
        gap: 10px;
        position: relative;
        padding: 0;
    }
    .chat-sessions {
        width: 200px;
        height: 100%;
        display: flex;
        flex-direction: column;
        box-sizing: border-box;
        overflow-y: auto;
        text-align: start;
        border-right: 1px solid var(--bg-1);
        color: var(--fg-1);
    }

    h3 {
        margin-bottom: 0;
        padding: 0 10px 10px 10px;
        border-bottom: 1px solid var(--bg-1);
        white-space: nowrap;
    }

    .session-item {
        color: var(--fg-1);
        font-size: 0.8rem;
        padding: 10px 10px 0 10px;
        position: relative;

        &:hover, &:focus, &:active {
            color: var(--fg-2);
        }
    }

    .session-item button {
        position: absolute;
        top: 13px;
        right: 5px;
        display: none;
        color: rgb(235, 22, 22);
    }

    .session-item:hover button {
        display: block;
    }

    .main-chat-container {
        width: 83%;
        height: 100%;
        position: relative;
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
        padding: 0 20px 20px 20px;
    }

    .clear-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 5px;
        position: absolute;
        bottom: 60px;
        left: 60%;
        right: 25%;
        transform: translate(-50%, -50%);
        padding: 5px 10px;
        border-radius: 25px;
        border: 1px solid var(--fg-2);
        color: var(--bg-1);
        background-color: var(--fg-2);
        font-size: 0.8rem;
        cursor: pointer;
        width: fit-content;

        &:hover {
            background-color: var(--fg-1);
        }
    }
</style>
