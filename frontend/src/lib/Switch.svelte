<script lang="ts">
  interface Props {
    label?: string;
    name?: string;
    checked?: boolean;
    disabled?: boolean;
    onChange?: any;
  }

  let {
    label = "",
    name = "",
    checked = $bindable(false),
    disabled = false,
    onChange = (e: CustomEvent<boolean>) => {}
  }: Props = $props();

  function toggle() {
    if (!disabled) {
      checked = !checked;
      onChange(new CustomEvent('change', { detail: checked }));
    }
  }
</script>

<div class="switch-container">
  <label for={name}>{label}</label>
  <button
    class={`switch ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}`}
    onclick={toggle}
    {disabled}
    aria-checked={checked}
    role="switch"
  >
    <span class="slider"></span>
  </button>
</div>

<style>
  .switch-container {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    width: 100%;
  }
  .switch {
    position: relative;
    display: inline-block;
    width: 50px;
    height: 27px;
    padding: 0;
    border: none;
    background-color: var(--bg-3);
    cursor: pointer;
    border-radius: 27px;
    transition: background-color 0.4s;
  }

  .switch.checked {
    background-color: var(--fg-1);
  }

  .switch:focus {
    outline: none;
    box-shadow: 0 0 0 2px var(--fg-1);
  }

  .slider {
    position: absolute;
    height: 20px;
    width: 20px;
    left: 4px;
    bottom: 4px;
    background-color: var(--bg-1);
    transition: 0.4s;
    border-radius: 50%;
  }

  .switch.checked .slider {
    transform: translateX(22px);
  }

  .switch.disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
