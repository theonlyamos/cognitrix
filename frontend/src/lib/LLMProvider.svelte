<script lang="ts">
  import Switch from "./Switch.svelte";

  export let model: string;
  export let api_key: string;
  export let base_url: string = "";
  export let temperature: Number;
  export let max_tokens: Number = 2086;
  export let is_multimodal: boolean = false;
</script>

<div class="form-group">
  <label for="model">Model</label>
  <input type="text" bind:value={model} />
</div>
<div class="form-group">
  <label for="api_key">API Key</label>
  <input type="text" bind:value={api_key} />
</div>
<div class="form-group">
  <label for="base_url">Base Url</label>
  <input type="text" bind:value={base_url} />
</div>
<div class="form-group">
  <label for="temperature">Temperature</label>
  <div class="range-container">
    <input
      type="range"
      id="temperature"
      name="temperature"
      min="0"
      max="1"
      step="0.1"
      bind:value={temperature}
    />
    <span>{temperature.toFixed(1)}</span>
  </div>
</div>
<div class="form-group">
  <label for="max_tokens">Maximum Output Tokens</label>
  <input type="number" bind:value={max_tokens} />
</div>
<div class="form-group">
  <Switch label="Is Multimodal" bind:checked={is_multimodal} disabled={true} />
</div>

<style>
  .range-container {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  input[type="range"] {
    flex-grow: 1;
  }

  .range-container span {
    min-width: 30px;
    text-align: right;
  }
</style>
