<script lang="ts">
  import { afterUpdate, beforeUpdate } from "svelte";
  import type { MessageInterface } from "../common/interfaces";
  import MessageComponent from "./Message.svelte";

  export let messages: MessageInterface[];

  let loading: boolean = true;
  let loadedMessages = [];
  let container: HTMLElement;

  let autoscroll = false;

  beforeUpdate(() => {
    if (container) {
      const scrollableDistance =
        container.scrollHeight - container.offsetHeight;
      autoscroll = container.scrollTop > scrollableDistance - 20;
    }
  });

  afterUpdate(() => {
    if (autoscroll) {
      container.scrollTo(0, container.scrollHeight);
    }
  });

  $: if (messages.length) {
    console.log(messages.length, loadedMessages.length);
    if (messages.length != loadedMessages.length) {
      loadedMessages = messages;
      loading = false;
    }
    if (autoscroll) {
      container.scrollTo(0, container.scrollHeight);
    }
  }
</script>

<div class="main-chat-container">
  <div class="chat-content" bind:this={container}>
    {#if loading}
      <div class="loading">
        <i class="fas fa-spinner fa-spin fa-3x"></i>
      </div>
    {/if}
    {#each messages as message, index (index)}
      <MessageComponent {...message} />
    {/each}
  </div>
  <slot />
</div>

<style>
  .main-chat-container {
    display: flex;
    flex: 1;
    flex-direction: column;
    height: 100%;
    min-width: 0;
    position: relative;
  }

  .chat-content {
    container-type: size;
    display: flex;
    flex-direction: column;
    -webkit-box-flex: 1;
    flex-grow: 1;
    position: relative;
    overflow: hidden auto;
    margin: 20px;
    gap: 20px;
  }
</style>
