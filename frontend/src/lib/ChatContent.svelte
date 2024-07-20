<script lang="ts">
    import { afterUpdate, beforeUpdate } from "svelte"; 
    import type { MessageInterface } from "../common/interfaces";
    import  MessageComponent from "./Message.svelte";
    import type { MouseEventHandler } from "svelte/elements";

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

<style>
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
