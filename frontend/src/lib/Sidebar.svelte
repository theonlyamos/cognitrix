<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import HomeIcon from '../assets/home.svg';
  import AgentsIcon from '../assets/agent.svg';
  import ToolBoxIcon from '../assets/toolbox.svg';
  import { link, useLocation } from 'svelte-routing';

  let page = window.location.pathname;
  let theme = 'light';

  onMount(() => {
    theme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', theme);
  });

  function toggleTheme() {
    theme = theme === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }

  const locationSub = useLocation().subscribe((location)=>{
    page = location.pathname
  })

  onDestroy(locationSub);
</script>

<aside>
  <nav>
    <a href="/" use:link class={page === '/' ? 'active' : ''}>
      <img src={HomeIcon} class="icon" alt="home link" />
      <span>Home</span>
    </a>
    <a href="/agents" use:link class={page.includes('/agents') ? 'active' : ''}>
      <img src={AgentsIcon} class="icon" alt="agents link" />
      <span>Agents</span>
    </a>
    <a href="/tools" use:link class={page.includes('/tools') ? 'active' : ''}>
      <img src={ToolBoxIcon} class="icon" alt="tools link" />
      <span>Tools</span>
    </a>
  </nav>

  <button on:click={toggleTheme}>
    {#if theme === 'light'}
    <i class="fas fa-moon"></i>
    {:else}
    <i class="fas fa-sun"></i>
    {/if}
  </button>
</aside>

<style>
  aside {
    background: var(--bg-2);
    border-radius: 7px;
    text-align: start;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-shadow: var(--shadow-sm);
  }

  nav {
    display: flex;
    flex-direction: column;
    justify-content: start;
  }

  nav a {
    padding: 20px;
    text-align: center;
    color: var(--fg-2);

    &:first-of-type {
      border-radius: 7px 7px 0 0;
    }

    &:hover, &.active {
      background-color: var(--bg-1);
      color: var(--fg-1);
    }
  }

  button {
    padding: 0;
    width: fit-content;
    background-color: var(--bg-2);
    display: flex;
    margin: 0 auto;
    margin-bottom: 20px;
    border: none;
    outline: none;

    &:hover, &:focus, &:focus-visible {
      border: none;
      padding: 0 !important;
      outline: none;
    }
  }

  button i {
    color: var(--fg-1);
    font-size: 1.2rem;
    align-self: flex-start;

    &&.fa-moon {
      align-self: flex-end;
    }
  }
</style>