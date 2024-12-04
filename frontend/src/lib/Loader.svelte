<script lang="ts">
  interface Props {
    message?: string;
  }

  let { message = "" }: Props = $props();
</script>

<div class="loader">
  {#if message}
    <p>{message}</p>
  {/if}
  <div class="spinner"></div>
</div>

<style>
  .loader {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    margin: 0 auto;
    inline-size: fit-content;
  }

  .spinner {
    border: 4px solid var(--bg-2);
    border-top: 4px solid var(--fg-1);
    border-radius: 50%;
    width: 40px;
    height: 40px;
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }
</style>
