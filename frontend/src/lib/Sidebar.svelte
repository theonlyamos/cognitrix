<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import HomeIcon from "../assets/home.svg";
  import AgentsIcon from "../assets/agent.svg";
  import { link, useLocation, navigate } from "svelte-routing";
  import { userStore } from "../common/stores";

  let page: string = window.location.pathname;
  let theme: string;

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
    // Initialize theme
    initializeTheme();
  });

  function toggleTheme() {
    const currentTheme = localStorage.getItem("theme") || getSystemTheme();
    const newTheme = currentTheme === "light" ? "dark" : "light";
    setTheme(newTheme);
  }

  // Listen for system theme changes
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
</script>

<aside>
  <nav>
    <a href="/" use:link class={page === "/" ? "active" : ""}>
      <img src={HomeIcon} class="icon" alt="home link" />
      <span>Home</span>
    </a>
    <a href="/agents" use:link class={page.includes("/agents") ? "active" : ""}>
      <img src={AgentsIcon} class="icon" alt="agents link" />
      <span>Agents</span>
    </a>
    <a href="/tasks" use:link class={page.includes("/tasks") ? "active" : ""}>
      <i class="fa-solid fa-tools fa-fw"></i>
      <span>Tasks</span>
    </a>
    <a href="/teams" use:link class={page.includes("/teams") ? "active" : ""}>
      <i class="fa-solid fa-users fa-fw"></i>
      <span>Teams</span>
    </a>
  </nav>

  <button on:click={toggleTheme}>
    {#if theme === "light"}
      <i class="fas fa-moon"></i>
    {:else}
      <i class="fas fa-sun"></i>
    {/if}
  </button>

  <button on:click={handleLogout} title="Logout">
    <i class="fas fa-power-off"></i>
  </button>
</aside>

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
    inset-inline-start: 0;
    z-index: 9999;
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
    color: var(--fg-1);
    font-size: 1.2rem;
    align-self: flex-start;

    &&.fa-moon {
      align-self: flex-end;
    }
  }

  .fa-power-off {
    color: #f44336;
  }

  @media screen and (max-width: 640px) {
    aside {
      /* display: none; */
      /* position: absolute;
      inset-block-start: 0;
      inset-inline-start: 0;
      block-size: inherit; */
      /* transform: translateY(-50%); */
      /* z-index: 20; */
      margin-inline-start: -120px;
      box-shadow: var(--shadow-sm);
    }
  }
</style>
