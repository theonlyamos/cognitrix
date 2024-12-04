<script lang="ts">
  import { stopPropagation } from 'svelte/legacy';

  interface Props {
    isOpen?: boolean;
    onClose: () => void;
    type?: "alert" | "confirm" | "info" | "success" | "warning";
    appearance?: "default" | "minimal" | "bordered" | "floating";
    size?: "small" | "medium" | "large";
    action?: (() => void) | null;
    actionLabel?: string;
    title?: string;
    children?: import('svelte').Snippet;
  }

  let {
    isOpen = $bindable(false),
    onClose,
    type = "info",
    appearance = "default",
    size = "medium",
    action = null,
    actionLabel = "Confirm",
    title = "",
    children
  }: Props = $props();

  function handleClose() {
    isOpen = false;
    onClose();
  }

  function handleOutsideClick(event: MouseEvent) {
    if (event.target === event.currentTarget) {
      handleClose();
    }
  }

  function handleModalContentClick(event: MouseEvent) {
    event.stopPropagation();
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

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      handleClose();
    }
  }

  function handleConfirmKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ' ') {
      handleAction();
    }
  }

  function handleCancelKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ' ') {
      handleClose();
    }
  }
</script>

{#if isOpen}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    class="modal-overlay"
    onclick={handleOutsideClick}
    onkeydown={handleKeydown}
    role="dialog"
    aria-modal="true"
    tabindex="-1"
  >
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="modal-content card {getTypeClass(type)} {getAppearanceClass(
        appearance,
      )} {getSizeClass(size)}"
      style="--modal-color: {getTypeColor(type)};"
      onclick={stopPropagation(handleModalContentClick)}
    >
      <div class="modal-header">
        <div class="modal-title">{title}</div>
        <button
          class="close-btn"
          onclick={handleClose}
          onkeydown={handleCancelKeydown}
          aria-label="Close modal"
        >
          &times;
        </button>
      </div>
      {@render children?.()}
      <div class="action-buttons">
        <button
          class="cancel-btn"
          onclick={handleClose}
          onkeydown={handleCancelKeydown}
        >
          Cancel
        </button>
        <button
          class="confirm-btn"
          onclick={handleAction}
          onkeydown={handleConfirmKeydown}
        >
          {actionLabel}
        </button>
      </div>
    </div>
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

  .modal-overlay:focus {
    outline: none;
  }
</style>
