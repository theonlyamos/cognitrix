<script>
  import "../app.css";
  import ThemeToggle from "$lib/ThemeToggle.svelte";
  /** @type {{children?: import('svelte').Snippet}} */
  let { children } = $props();
</script>

<div class="app">
  <div class="absolute top-4 right-4">
    <ThemeToggle />
  </div>
  {@render children?.()}
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }
</style>
