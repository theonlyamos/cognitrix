<script lang="ts">
  interface Props {
    label?: string;
    name?: string;
    value?: any;
    checked?: boolean;
    disabled?: boolean;
    group?: string[];
    onChange?: any;
  }

  let {
    label = "",
    name = "",
    value = "",
    checked = $bindable(false),
    disabled = false,
    group = $bindable([]),
    onChange = (e: Event) => {}
  }: Props = $props();

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

<label class="radio">
  <input
    type="radio"
    {value}
    {name}
    {checked}
    bind:group
    onchange={onChange}
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
  {label}
</label>

<style>
  .radio {
    display: flex;
    gap: 10px;
    font-size: 1rem;
    text-align: start;
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
