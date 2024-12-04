<script lang="ts">
    import { slide } from "svelte/transition";

  interface Props {
    title?: string;
    opened?: boolean;
    children?: import('svelte').Snippet;
  }

  let { title = "", opened = $bindable(false), children }: Props = $props();
</script>

<div class="accordion">
  <div class="accordion-content">
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <header role="button" onclick={() => (opened = !opened)} tabindex="0">
      <label for="tools">{title}</label>
      <button class="toggle-tools">
        <i
          class={`fa-solid ${opened ? "fa-angle-up fa-fw" : "fa-angle-down fa-fw"}`}
        ></i>
      </button>
    </header>
  </div>

  {#if opened}
    <div class="content">
      {@render children?.()}
    </div>
  {/if}
</div>

<style>
  .accordion {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .content {
    display: grid;
    background-color: var(--bg-1);
    border-radius: 5px;
    gap: 15px;
    padding: 10px;
  }
</style>
