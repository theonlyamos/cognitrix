<script lang="ts">
    export let title: string = '';
    let opened: boolean = false;

</script>
<div class="form-group">
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <header role="button" on:click={() => opened = !opened} tabindex="0">
        <label for="tools">{title}</label>
        <button  class="toggle-tools"> 
            <i 
                class={`fa-solid ${opened ? 'fa-angle-up fa-fw' : 'fa-angle-down fa-fw'}`}
            ></i>
        </button>
    </header>
</div>

{#if opened}
<slot/>
{/if}