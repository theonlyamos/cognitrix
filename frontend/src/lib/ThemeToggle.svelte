<script lang="ts">
  import { onMount } from "svelte";

  let darkMode = $state(false);

  function toggleTheme() {
    darkMode = !darkMode;
    localStorage.setItem("darkMode", darkMode ? "true" : "false");
    updateTheme();
  }

  function updateTheme() {
    if (darkMode) {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  }

  onMount(() => {
    const savedTheme = localStorage.getItem("darkMode");
    darkMode = savedTheme === "true";
    updateTheme();
  });
</script>

<button
  onclick={toggleTheme}
  class="p-2 rounded-full bg-gray-200 dark:bg-gray-700"
>
  {#if darkMode}
    🌙
  {:else}
    ☀️
  {/if}
</button>
