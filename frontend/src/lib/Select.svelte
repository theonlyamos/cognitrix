<script lang="ts">
  export let options: { value: string; label: string }[] = [];
  export let value: string = "";
  export let placeholder: string = "Select an option";

  function handleChange(event: Event) {
    const target = event.target as HTMLSelectElement;
    value = target.value;
  }
</script>

<select {value} on:change={handleChange}>
  <option value="" disabled selected>{placeholder}</option>
  {#each options as option}
    <option value={option.value}>{option.label}</option>
  {/each}
</select>

<style>
  select {
    width: 100%;
    padding: 8px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    background-color: var(--bg-1);
    color: var(--fg-1);
    font-size: 1em;
  }

  select:focus {
    outline: none;
    border-color: var(--accent-color);
  }
</style>
