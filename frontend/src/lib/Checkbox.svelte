<script lang="ts">
  export let label = "";
  export let name = "";
  export let value: any = "";
  export let checked: boolean = false;
  export let group: string[] = [];
  export let onChange = (e: Event) => {};

  const handleChange = (event: Event) => {
    const target = event.target as HTMLInputElement;
    checked = target.checked;
    if (checked) {
      group.push(value);
    } else {
      const index = group.indexOf(value);
      if (index > -1) {
        group.splice(index, 1);
      }
    }
  };
</script>

<label class="checkbox">
  <input
    type="checkbox"
    {value}
    {name}
    bind:checked
    bind:group
    on:change={onChange}
  />
  <span class="checkbox-icon">
    <i
      class="fa-solid fa-square-check icon-checked"
      style="display: {checked ? 'inline-block' : 'none'};"
    ></i>
    <i
      class="fa-regular fa-square icon-unchecked"
      style="display: {checked ? 'none' : 'inline-block'};"
    ></i>
  </span>
  {label}
</label>

<style>
  .checkbox {
    display: flex;
    align-items: flex-start;
    text-align: start;
    text-align: start;
  }

  .checkbox-icon {
    position: relative;
    width: 24px;
    height: 24px;
    margin-right: 8px;
  }

  .checkbox input {
    opacity: 0;
    width: 0;
    height: 0;
    display: none;
  }

  .icon-checked,
  .icon-unchecked {
    position: absolute;
    top: 0;
    left: 0;
    font-size: 24px;
  }

  .icon-checked {
    color: var(--fg-1);
  }

  .icon-unchecked {
    color: var(--fg-2);
  }
</style>
