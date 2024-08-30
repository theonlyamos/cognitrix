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
  // import { sseStore } from './common/stores';
  import { webSocketStore } from "./common/stores";

  webSocketStore.connect();
</script>

<Router>
  <div class="container">
    <Sidebar />
    <Container>
      <Route path="/" component={Home} />
      <Route path="/:session_id" let:params>
        <Home session_id={params?.session_id} />
      </Route>
      <Route path="/c/:agent_id" let:params>
        <Home agent_id={params?.agent_id} />
      </Route>
      <Route path="/agents" component={Agents} />
      <Route path="/agents/new" component={AgentPage} />
      <Route path="/agents/:agent_id" let:params>
        <AgentPage agent_id={params?.agent_id} />
      </Route>
      <Route path="/tasks" component={Tasks} />
      <Route path="/tasks/new" component={TaskPage} />
      <Route path="/tasks/:task_id" let:params>
        <TaskPage task_id={params?.task_id} />
      </Route>
      <Route path="/teams" component={Teams} />
      <Route path="/teams/new" component={TeamPage} />
      <Route path="/teams/:team_id" let:params>
        <TeamPage team_id={params?.team_id} />
      </Route>
    </Container>
  </div>
</Router>

<style>
  .container {
    position: relative;
    /* margin: 0 auto; */
    display: flex;
    justify-content: space-between;
    /* border-radius: 25px; */
    gap: 20px;
  }
</style>
