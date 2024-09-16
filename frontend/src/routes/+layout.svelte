<script>
  import "../app.css";
  import ThemeToggle from "$lib/ThemeToggle.svelte";
</script>

<div class="app">
  <div class="absolute top-4 right-4">
    <ThemeToggle />
  </div>
  <slot />
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }
</style>
