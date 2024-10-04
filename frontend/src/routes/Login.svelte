<script lang="ts">
  import { navigate, link } from "svelte-routing";
  import { API_BACKEND_URI } from "../common/constants";
  import { userStore } from "../common/stores";

  let username = "";
  let password = "";
  let error = "";

  async function handleLogin() {
    try {
      const response = await fetch(`${API_BACKEND_URI}/auth/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ username, password }),
      });

      if (response.ok) {
        const data = await response.json();
        userStore.login(data.user, data.access_token);
        navigate('/')
        // window.location.reload();
      } else {
        const data = await response.json();
        error = "Login failed";
      }
    } catch (err) {
      error = "An error occurred during login";
    }
  }
</script>

<div class="container">
  <h1>Log In</h1>
  <form on:submit|preventDefault={handleLogin}>
    <div class="form-group">
      <label for="email">Email</label>
      <input type="email" id="email" bind:value={username} required />
    </div>
    <div class="form-group">
      <label for="password">Password</label>
      <input type="password" id="password" bind:value={password} required />
    </div>
    {#if error}
      <p class="error">{error}</p>
    {/if}
    <button type="submit" class="btn">Log In</button>
  </form>
  <p>Don't have an account? <a href="/signup" use:link>Sign up</a></p>
</div>

<style>
  .container {
    max-inline-size: 400px;
    margin-inline: auto;
    padding-block: 2rem;
    padding-inline: 1rem;
  }

  h1 {
    text-align: center;
    margin-block-end: 2rem;
  }

  .form-group {
    margin-block-end: 1rem;
  }

  label {
    display: block;
    margin-block-end: 0.5rem;
  }

  input {
    inline-size: 100%;
    padding-block: 0.5rem;
    padding-inline: 0.75rem;
    border: 1px solid var(--bg-2);
    border-radius: 4px;
    background-color: var(--bg-1);
    color: var(--fg-1);
  }

  .error {
    color: #ff3e00;
    margin-block-end: 1rem;
  }

  .btn {
    inline-size: 100%;
    padding-block: 0.75rem;
    background-color: var(--fg-1);
    color: var(--bg-1);
    border: none;
    border-radius: 4px;
    cursor: pointer;
    transition: background-color 0.3s ease;
  }

  .btn:hover {
    background-color: var(--fg-2);
  }

  p {
    text-align: center;
    margin-block-start: 1rem;
  }

  a {
    color: var(--fg-1);
    text-decoration: none;
  }

  a:hover {
    text-decoration: underline;
  }
</style>
