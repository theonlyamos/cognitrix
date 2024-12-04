<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import HomeIcon from "../assets/home.svg";
  import AgentsIcon from "../assets/agent.svg";
  import { link, useLocation, navigate } from "svelte-routing";
  import { userStore } from "../common/stores";

  let page: string = $state(window.location.pathname);
  let theme: string = $state();
  let isSidebarOpen = $state(true);

  function setTheme(newTheme: string) {
    theme = newTheme;
    document.documentElement.setAttribute("data-theme", newTheme);
    localStorage.setItem("theme", newTheme);
  }

  function getSystemTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function initializeTheme() {
    const savedTheme = localStorage.getItem("theme");
    if (savedTheme) {
      setTheme(savedTheme);
    } else {
      setTheme(getSystemTheme());
    }
  }

  onMount(() => {
    initializeTheme();
  });

  function toggleTheme() {
    const currentTheme = localStorage.getItem("theme") || getSystemTheme();
    const newTheme = currentTheme === "light" ? "dark" : "light";
    setTheme(newTheme);
  }

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", (e) => {
      if (!localStorage.getItem("theme")) {
        setTheme(e.matches ? "dark" : "light");
      }
    });

  const locationSub = useLocation().subscribe((location) => {
    page = location.pathname;
  });

  onDestroy(locationSub);

  function handleLogout() {
    userStore.logout();
    navigate("/login");
  }

  function handleSidebar() {
    isSidebarOpen = !isSidebarOpen;
  }
</script>

<aside class:closed={!isSidebarOpen} class:open={isSidebarOpen}>
  <nav>
    <a href="/" use:link class={page === "/" ? "active" : ""}>
      <img src={HomeIcon} class="icon" alt="home link" />
      <span>Home</span>
    </a>
    <a
      href="/agents"
      use:link
      class={page.startsWith("/agents") ? "active" : ""}
    >
      <img src={AgentsIcon} class="icon" alt="agents link" />
      <span>Agents</span>
    </a>
    <a href="/tasks" use:link class={page.startsWith("/tasks") ? "active" : ""}>
      <i class="fa-solid fa-tools fa-fw"></i>
      <span>Tasks</span>
    </a>
    <a href="/teams" use:link class={page.startsWith("/teams") ? "active" : ""}>
      <i class="fa-solid fa-users fa-fw"></i>
      <span>Teams</span>
    </a>
  </nav>

  <button onclick={toggleTheme}>
    {#if theme === "light"}
      <i class="fas fa-moon"></i>
    {:else}
      <i class="fas fa-sun"></i>
    {/if}
  </button>

  <button onclick={handleSidebar} title="Sidebar">
    <i class="fas fa-bars"></i>
  </button>

  <button onclick={handleLogout} title="Logout">
    <i class="fas fa-power-off"></i>
  </button>
</aside>

<button onclick={handleSidebar} class="toggle-off" title="Sidebar">
  <i class="fas fa-bars-staggered"></i>
</button>

<style>
  aside {
    block-size: 100vh;
    background: var(--bg-1);
    text-align: start;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-shadow: var(--shadow-sm);
    transition: all 0.2s ease-in-out;
    position: sticky;
    inset-block-start: 0;
    z-index: 9999;
    transition: margin-inline-start 0.3s ease-in-out;
    width: 100px;
  }

  aside.open {
    margin-inline-start: 0;
    inset-inline-start: 0;
  }

  aside.closed {
    margin-inline-start: -100px;
    inset-inline-start: -100px;
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
    display: flex;
    flex-direction: column;
    gap: 10px;
    align-items: center;

    &:hover,
    &.active {
      background-color: var(--fg-2);
      color: var(--bg-1);
    }
  }

  nav a i {
    font-size: 1.5rem;
  }

  button {
    padding: 0;
    width: fit-content;
    background-color: var(--bg-1);
    display: flex;
    margin: 0 auto;
    margin-bottom: 20px;
    border: none;
    outline: none;

    &:hover,
    &:focus,
    &:focus-visible {
      border: none;
      padding: 0 !important;
      outline: none;
    }
  }

  button i {
    color: var(--fg-2);
    font-size: 1.2rem;
    align-self: flex-start;

    &&.fa-moon {
      align-self: flex-end;
    }
  }

  button.toggle-off {
    position: fixed;
    inset-block-end: 100px;
    inset-inline-start: 0;
    z-index: 20;
    background-color: var(--fg-1);
    box-shadow: var(--shadow-sm);
    inline-size: fit-content;
    display: flex;
    justify-content: flex-end;
    padding: 5px;
    border-top-right-radius: 10px;
    border-bottom-right-radius: 10px;

    &:hover,
    &:focus,
    &:focus-visible {
      border: inherit !important;
      padding: 5px !important;
    }
  }

  button.toggle-off i {
    &:hover,
    &:focus,
    &:focus-visible {
      color: var(--bg-1);
    }
  }

  .fa-power-off {
    color: #f44336;
  }

  @media screen and (max-width: 640px) {
    aside {
      position: absolute;
      inset-block-start: 0;
      inset-inline-start: 0;
      block-size: inherit;
    }
  }
</style>
