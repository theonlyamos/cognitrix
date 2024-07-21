<script lang="ts">
  export let label: string = "";
  export let name: string = "";
  export let checked: boolean = false;
  export let group: string[] = [];
  export let disabled: boolean = false;
  export let onChange = (e: Event) => {};
</script>

<div class="switch-container">
  <label for={name}>{label}</label>
  <label class={`switch ${disabled ? "disabled" : ""}`}>
    <input
      type="checkbox"
      {name}
      bind:checked
      bind:group
      on:change={onChange}
      {disabled}
    />
    <span class="slider round"> </span>
  </label>
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
  }

  .switch input {
    opacity: 0;
    width: 0;
    height: 0;
  }

  .slider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: var(--bg-3);
    -webkit-transition: 0.4s;
    transition: 0.4s;
  }

  .slider:before {
    position: absolute;
    content: "";
    height: 20px;
    width: 20px;
    left: 4px;
    bottom: 4px;
    background-color: var(--bg-1);
    -webkit-transition: 0.4s;
    transition: 0.4s;
  }

  input:checked + .slider {
    background-color: var(--fg-1);
  }

  input:focus + .slider {
    box-shadow: 0 0 1px var(--fg-1);
  }

  input:checked + .slider:before {
    -webkit-transform: translateX(22px);
    -ms-transform: translateX(22px);
    transform: translateX(22px);
  }

  .slider.round {
    border-radius: 27px;
  }

  .slider.round:before {
    border-radius: 50%;
  }
</style>
