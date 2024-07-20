<script lang="ts">
  import { link } from "svelte-routing";

    const tasks: object[] = [];
</script>

<div class="toolbar">
    <a href="/tasks/new" use:link class="btn">
        <i class="fa-solid fa-tools fa-fw"></i>
        <span>New Task</span>
    </a>
</div>

<style>
</style>