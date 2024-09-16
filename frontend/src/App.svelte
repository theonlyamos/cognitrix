<script lang="ts">
  import { Router, Route } from "svelte-routing";
  import Sidebar from "./lib/Sidebar.svelte";
  import Home from "./routes/Home.svelte";
  import Agents from "./routes/Agents.svelte";
  import Container from "./lib/Container.svelte";
  import AgentPage from "./routes/AgentPage.svelte";
  import Tasks from "./routes/Tasks.svelte";
  import TaskPage from "./routes/TaskPage.svelte";
  import Teams from "./routes/Teams.svelte";
  import TeamPage from "./routes/TeamPage.svelte";
  import Signup from "./routes/Signup.svelte";
  import Login from "./routes/Login.svelte";
  import { webSocketStore, userStore } from "./common/stores";
  import { onMount } from "svelte";
  import ThemeToggle from "$lib/ThemeToggle.svelte";
  import "./app.css";

  let user: any;

  userStore.subscribe((value) => {
    user = value;
    console.log("User:", user);
  });

  onMount(() => {
    userStore.checkAuth();
    webSocketStore.connect();
  });
</script>

<Router>
  <div class="container">
    {#if user}
      <Sidebar></Sidebar>
    {/if}
    <Container>
      <Route path="/" component={user ? Home : Login}></Route>
      <Route path="/:session_id" component={Home}></Route>
      <Route path="/c/:agent_id" component={Home}></Route>
      <Route path="/signup" component={Signup}></Route>
      <Route path="/login" component={Login}></Route>
      <Route path="/agents" component={Agents}></Route>
      <Route path="/agents/new" component={AgentPage}></Route>
      <Route path="/agents/:agent_id" component={AgentPage}></Route>
      <Route path="/tasks" component={Tasks}></Route>
      <Route path="/tasks/new" component={TaskPage}></Route>
      <Route path="/tasks/:task_id" component={TaskPage}></Route>
      <Route path="/teams" component={Teams}></Route>
      <Route path="/teams/new" component={TeamPage}></Route>
      <Route path="/teams/:team_id" component={TeamPage}></Route>
    </Container>
  </div>
</Router>

<style>
  .container {
    position: relative;
    display: flex;
    justify-content: space-between;
    gap: 20px;
  }
</style>
