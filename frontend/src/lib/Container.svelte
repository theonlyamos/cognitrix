<script lang="ts">
  interface Props {
    children?: import('svelte').Snippet;
  }

  let { children }: Props = $props();</script>

<main>
  {@render children?.()}
</main>

<style>
  main {
    /* inline-size: 90%;
    border-radius: 7px;
    background: var(--bg-2); */
    /* box-shadow: var(--shadow-sm); */
    /* overflow: hidden auto; */
    box-sizing: border-box;
    position: relative;
    flex: 1;
    overflow-y: auto;
  }
</style>
