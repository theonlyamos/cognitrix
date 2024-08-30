<script lang="ts">
  export let title: string = "";
  export let opened: boolean = false;
</script>

<div class="form-group">
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <header role="button" on:click={() => (opened = !opened)} tabindex="0">
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
    <slot />
  </div>
{/if}

<style>
  .content {
    display: grid;
    background-color: var(--bg-1);
    padding: 10px;
    border-radius: 5px;
    gap: 15px;
  }
</style>
