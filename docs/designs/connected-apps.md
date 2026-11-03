# Connected apps

Settings › Connected apps lets each person sign in to hosted MCP servers (GitHub, Gmail, Google Calendar, Drive, Docs, Sheets, Slides, Notion, Linear, Sentry, or any server added by address). Their agents can then use those apps' tools.

## How it works

- Each connection is an `MCPServer` row owned by the person (`catalog_id` names the catalog entry, `auth_type` is `oauth`, or `header` for a GitHub token). Nobody else can see or use it. Agents pick it on their Connections tab like any MCP server.
- Sign-in is OAuth 2.1 with PKCE (`cognitrix/mcp/oauth.py`). The web app opens the sign-in page in a popup. The desktop app opens it in the system browser. Both poll `GET /api/v1/mcp/oauth/flows/{id}` until it's done.
- Tokens are encrypted with the saved-secrets key, refreshed shortly before they expire, and sent as `Authorization: Bearer` when the server connects. A live connection is reopened with a fresh token before the old one runs out.
- When a refresh is refused or the server answers 401, the app is marked `needs_reauth`. The owner gets one Inbox alert with a Reconnect button. Agent tool calls return `App sign-in needed: <App>.`, and chat shows a Reconnect card for that result.
- Background tasks use the agent owner's connections.
- Per agent, each tool can be set to Allow, Ask or Off. The default comes from the tool's MCP annotations: read-only tools run, anything else asks.

## Setting up the apps (admin)

**GitHub and Google** sign in through the OAuth apps the server already uses for "Sign in with GitHub / Google", and through the same callback address:

```
<base>/api/v1/auth/oauth/github/callback
<base>/api/v1/auth/oauth/google/callback
```

`<base>` is `COGNITRIX_PUBLIC_URL`, or `http://127.0.0.1:48210` in the desktop app. Set `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` and `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. The Connected apps page shows admins the exact redirect URI.

- **GitHub** uses the hosted server at `https://api.githubcopilot.com/mcp/` with the scopes `repo read:org read:user`. Organizations may have to approve the OAuth app. Anyone can paste a personal access token instead.
- **Google** uses Google's Workspace MCP servers (developer preview), for example `https://gmailmcp.googleapis.com/mcp/v1`. In the Google Cloud project that owns the client ID:
  1. Turn on each app's MCP API (APIs & Services › Library, search for "Gmail MCP API", "Google Calendar MCP API" and so on).
  2. Add the scopes from `cognitrix/mcp/catalog.py` to the OAuth consent screen.
  3. Use a "Web application" client and add the redirect URI above.

**Notion, Linear, Sentry and servers added by address** need no setup. Cognitrix reads the server's sign-in metadata and registers itself, with a client ID metadata document when the server has a public https address, or dynamic client registration otherwise. On a shared server, every address in that metadata must be public https.

## Limits

- Pending sign-ins are kept in memory for ten minutes, like "Sign in with Google". Run one API process, or keep a sign-in on the process that started it.
- Google Calendar's MCP scopes are read-only today.
