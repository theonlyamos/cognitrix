<script lang="ts">
  export let label = "";
  export let name = "";
  export let value: any = "";
  export let checked: boolean = false;
  export let disabled: boolean = false;
  export let group: string[] = [];
  export let onChange = (e: Event) => {};

  const handleChange = (event: Event) => {
    // const target = event.target as HTMLInputElement;
    // checked = target.checked;
    // console.log(checked);
    // if (checked) {
    //   group.push(value);
    // } else {
    //   const index = group.indexOf(value);
    //   if (index > -1) {
    //     group.splice(index, 1);
    //   }
    // }
    if (!disabled) checked = !checked;
  };
</script>

<button class="radio" on:click={handleChange}>
  <input
    type="radio"
    {value}
    {name}
    {checked}
    bind:group
    on:change={onChange}
    {disabled}
  />
  <span class="radio-icon">
    <i
      class="fa-solid fa-check-circle icon-checked"
      style="display: {checked ? 'inline-block' : 'none'};"
    ></i>
    <i
      class="fa-regular fa-circle icon-unchecked"
      style="display: {checked ? 'none' : 'inline-block'};"
    ></i>
  </span>
  <label for="" title={label}>{label}</label>
</button>

<style>
  .radio {
    display: flex;
    text-align: start;
    gap: 10px;
  }

  .radio-icon {
    position: relative;
    width: 24px;
    height: 24px;
    margin-right: 8px;
  }

  .radio input {
    opacity: 0;
    width: 0;
    height: 0;
    display: none;
  }

  /* .icon-checked,
  .icon-unchecked {
    position: absolute;
    top: 0;
    left: 0;
    font-size: 16px;
  } */

  .icon-checked {
    color: var(--fg-1);
  }

  .icon-unchecked {
    color: var(--fg-2);
  }

  label {
    font-size: 0.875rem;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    cursor: pointer;
  }
</style>
