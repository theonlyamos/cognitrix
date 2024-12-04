<script lang="ts">
  import { preventDefault } from 'svelte/legacy';

  import { navigate, link } from "svelte-routing";
  import { API_BACKEND_URI } from "../common/constants";

  let name = $state("");
  let email = $state("");
  let password = $state("");
  let confirmPassword = $state("");
  let error = $state("");

  async function handleSignup() {
    if (password !== confirmPassword) {
      error = "Passwords do not match";
      return;
    }

    try {
      const response = await fetch(`${API_BACKEND_URI}/auth/signup`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ name, email, password }),
      });

      if (response.ok) {
        navigate("/login");
      } else {
        const data = await response.json();
        error = data.detail || "Signup failed";
      }
    } catch (err) {
      error = "An error occurred during signup";
    }
  }
</script>

<div class="container">
  <h1>Sign Up</h1>
  <form onsubmit={preventDefault(handleSignup)}>
    <div class="form-group">
      <label for="name">Name</label>
      <input type="text" id="name" bind:value={name} required />
    </div>
    <div class="form-group">
      <label for="email">Email</label>
      <input
        type="email"
        id="email"
        bind:value={email}
        required
        autocomplete="email"
      />
    </div>
    <div class="form-group">
      <label for="password">Password</label>
      <input type="password" id="password" bind:value={password} required />
    </div>
    <div class="form-group">
      <label for="confirmPassword">Confirm Password</label>
      <input
        type="password"
        id="confirmPassword"
        bind:value={confirmPassword}
        required
      />
    </div>
    {#if error}
      <p class="error">{error}</p>
    {/if}
    <button type="submit" class="btn">Sign Up</button>
  </form>
  <p>Already have an account? <a href="/login" use:link>Log in</a></p>
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
