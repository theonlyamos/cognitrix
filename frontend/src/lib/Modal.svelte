<script lang="ts">
  export let isOpen = false;
  export let onClose: () => void;
  export let type: "alert" | "confirm" | "info" | "success" | "warning" =
    "info";
  export let appearance: "default" | "minimal" | "bordered" | "floating" =
    "default";
  export let size: "small" | "medium" | "large" = "medium";
  export let action: (() => void) | null = null;
  export let actionLabel = "Confirm";
  export let title = "";

  function handleClose() {
    isOpen = false;
    onClose();
  }

  function handleOutsideClick(event: MouseEvent) {
    if (event.target === event.currentTarget) {
      handleClose();
    }
  }

  function getTypeClass(type: string): string {
    return `modal-${type}`;
  }

  function getAppearanceClass(appearance: string): string {
    return `modal-appearance-${appearance}`;
  }

  function getSizeClass(size: string): string {
    return `modal-size-${size}`;
  }

  function handleAction() {
    if (action) {
      action();
      handleClose();
    }
  }

  function getTypeColor(
    type: "alert" | "confirm" | "info" | "success" | "warning"
  ): string {
    const colorMap = {
      alert: "var(--color-alert)",
      confirm: "var(--color-confirm)",
      info: "var(--color-info)",
      success: "var(--color-success)",
      warning: "var(--color-warning)",
    } as const;
    return colorMap[type];
  }
</script>

{#if isOpen}
  <div class="modal-overlay">
    <button
      class="modal-overlay-button"
      on:click={handleOutsideClick}
      aria-label="Close modal"
    >
      <div
        class="modal-content card {getTypeClass(type)} {getAppearanceClass(
          appearance,
        )} {getSizeClass(size)}"
        style="--modal-color: {getTypeColor(type)};"
      >
        <div class="modal-header">
          <div class="modal-title">{title}</div>
          <button class="close-btn" on:click={handleClose}>&times;</button>
        </div>
        <slot></slot>
        <div class="action-buttons">
          <button class="cancel-btn" on:click={handleClose}>Cancel</button>
          <button class="confirm-btn" on:click={handleAction}
            >{actionLabel}</button
          >
        </div>
      </div>
    </button>
  </div>
{/if}

<style>
  .modal-overlay {
    position: fixed;
    inset: 0;
    inline-size: 100%;
    block-size: 100%;
    background-color: rgba(0, 0, 0, 0.3);
    backdrop-filter: blur(5px);
    -webkit-backdrop-filter: blur(5px); /* For Safari */
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 1000;
  }

  .modal-overlay-button {
    inline-size: 100%;
    block-size: 100%;
    background: none;
    border: none;
    padding: 0;
    margin: 0;
    display: flex;
    justify-content: center;
    align-items: center;
  }

  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .modal-title {
    font-size: 1.1rem;
    font-weight: 600;
  }

  .modal-content {
    position: relative;
    inline-size: 90%;
    max-block-size: 90vh;
    overflow-y: auto;
    background-color: var(--bg-1);
    border-radius: 8px;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    padding: 5px 20px 20px 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .modal-size-small {
    max-inline-size: 300px;
  }

  .modal-size-medium {
    max-inline-size: 500px;
  }

  .modal-size-large {
    max-inline-size: 800px;
  }

  .close-btn {
    font-size: 1.5rem;
    background: none;
    border: none;
    cursor: pointer;
    color: var(--modal-color);
  }

  .close-btn:hover {
    color: color-mix(in srgb, var(--modal-color) 80%, var(--fg-1));
  }

  .confirm-btn {
    background-color: var(--modal-color);
    color: var(--bg-1);
  }

  .cancel-btn {
    background-color: color-mix(in srgb, var(--modal-color) 20%, var(--bg-2));
    color: var(--modal-color);
  }

  .cancel-btn:hover,
  .confirm-btn:hover {
    opacity: 0.9;
  }

  .modal-appearance-default {
    background-color: var(--bg-1);
    border-radius: 8px;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    padding: 20px;
  }

  .modal-appearance-minimal {
    box-shadow: none;
    border: 1px solid var(--border-color);
  }
  .modal-appearance-bordered {
    border: 2px solid var(--modal-color);
  }
  .modal-appearance-floating {
    box-shadow: 0 8px 16px rgba(0, 0, 0, 0.1);
  }

  .action-buttons {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }

  .cancel-btn,
  .confirm-btn {
    padding: 8px 16px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
  }

  .cancel-btn {
    background-color: var(--bg-2);
    color: var(--fg-1);
  }

  .confirm-btn {
    background-color: var(--modal-color);
    color: var(--bg-1);
  }

  .cancel-btn:hover,
  .confirm-btn:hover {
    opacity: 0.9;
  }
</style>
