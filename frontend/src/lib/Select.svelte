<script lang="ts">
  interface Props {
    options?: { value: string; label: string }[];
    value?: string;
    placeholder?: string;
  }

  let { options = [], value = $bindable(""), placeholder = "Select an option" }: Props = $props();

  function handleChange(event: Event) {
    const target = event.target as HTMLSelectElement;
    value = target.value;
  }
</script>

<select {value} onchange={handleChange}>
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
