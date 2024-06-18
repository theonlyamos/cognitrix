<script lang="ts">
  import { Router, Route } from 'svelte-routing';
  import Sidebar from './lib/Sidebar.svelte';
  import Home from './routes/Home.svelte';
  import Agents from './routes/Agents.svelte';
  import Container from './lib/Container.svelte';
  
</script>

<Router>
  <div class="container">
    <Sidebar />
    <Container>
      <Route path="/" component={Home} />
      <Route path="/agents" component={Agents} />
    </Container>
  </div>
</Router>

<style>
  .container {
      margin: 50px;
      width: 90vw;
      height: 90vh;
      display: flex;
      justify-content: space-between;
      border-radius: 25px;
      gap: 20px;
  }
</style>


  
