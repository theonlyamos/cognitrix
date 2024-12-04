<script lang="ts">

  import { onMount } from 'svelte';
  interface Props {
    type?: "default" | "success" | "warning" | "danger" | "loading";
    message?: string;
    onClose?: (() => void) | null;
    autoClose?: boolean;
    autoCloseDelay?: number;
  }

  let {
    type = "default",
    message = "",
    onClose = null,
    autoClose = false,
    autoCloseDelay = 5000
  }: Props = $props();

  let visible = $state(true);

  function getTypeColor(type: "default" | "success" | "warning" | "danger" | "loading"): string {
    const colorMap = {
      default: "var(--color-info)",
      success: "var(--color-success)",
      warning: "var(--color-warning)",
      danger: "var(--color-alert)",
      loading: "var(--color-info)",
    } as const;
    return colorMap[type];
  }

  function handleClose() {
    visible = false;
    if (onClose) onClose();
  }

  onMount(() => {
    if (autoClose) {
      setTimeout(() => {
        handleClose();
      }, autoCloseDelay);
    }
  });
</script>

{#if visible}
  <div class="alert alert-{type}" style="--alert-color: {getTypeColor(type)};">
    <div class="alert-content">
      {#if type === "loading"}
        <div class="loading-spinner"></div>
      {/if}
      <span>{message}</span>
    </div>
    {#if onClose}
      <button class="close-btn" onclick={handleClose}>&times;</button>
    {/if}
  </div>
{/if}

<style>
  .alert {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-radius: 4px;
    background-color: color-mix(in srgb, var(--alert-color) 15%, var(--bg-1));
    color: var(--alert-color);
    margin-bottom: 16px;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  }

  .alert-content {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .loading-spinner {
    width: 20px;
    height: 20px;
    border: 2px solid var(--alert-color);
    border-top: 2px solid transparent;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }

  .close-btn {
    background: none;
    border: none;
    color: var(--alert-color);
    font-size: 1.2rem;
    cursor: pointer;
    padding: 0;
    margin-left: 12px;
  }

  .close-btn:hover {
    opacity: 0.8;
  }
</style>
