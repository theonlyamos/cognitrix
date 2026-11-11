# Connected apps

Settings › Connected apps lets each person sign in to hosted MCP servers (GitHub, Gmail, Google Calendar, Drive, Docs, Sheets, Slides, Slack, Notion, Linear, Sentry, or any server added by address). Their agents can then use those apps' tools.

## How it works

- Each connection is an `MCPServer` row owned by the person (`catalog_id` names the catalog entry, `auth_type` is `oauth`, or `header` for a GitHub token). Nobody else can see or use it. Agents pick it on their Connections tab like any MCP server.
- Sign-in is OAuth 2.1 with PKCE (`cognitrix/mcp/oauth.py`). The web app opens the sign-in page in a popup. The desktop app opens it in the system browser. Both poll `GET /api/v1/mcp/oauth/flows/{id}` until it's done.
- Tokens are encrypted with the saved-secrets key, refreshed shortly before they expire, and sent as `Authorization: Bearer` when the server connects. A live connection is reopened with a fresh token before the old one runs out.
- When a refresh is refused or the server answers 401, the app is marked `needs_reauth`. The owner gets one Inbox alert with a Reconnect button. Agent tool calls return `App sign-in needed: <App>.`, and chat shows a Reconnect card for that result.
- Background tasks use the agent owner's connections.
- Per agent, each tool can be set to Allow, Ask or Off. The default comes from the tool's MCP annotations: read-only tools run, anything else asks.

- Agents reach every app through two tools. `List Mcp Tools` finds tools: with a `query` it returns the best matches with their inputs, and with more than 25 tools connected it returns names only, so many apps don't fill the prompt. `Run Mcp Tool` runs one. The per-tool Allow, Ask and Off settings apply to the app tool being run, and tools set to Off are not listed.

## Extra permissions

Some apps have options a person turns on under the app once it is connected (`AppOption` in `cognitrix/mcp/catalog.py`):

- **Gmail › Send email** adds `send_email` and `send_draft`. The sign-in already covers it.
- **Google Calendar › Create and change events** adds `create_event`, `update_event` and `delete_event`. It needs the `calendar.events` scope, so the page offers "Reconnect to allow".

Google's MCP servers don't offer these, so they are built in (`cognitrix/mcp/native.py`) and call Google's REST API with the same sign-in. They are marked as changing things, so every call asks first unless the person sets the tool to Allow.

## Setting up the apps (admin)

Google, GitHub and Slack don't let Cognitrix register itself, so an admin creates an OAuth app with each once. **Settings › Connected apps › Set up sign-in** walks through it: the steps for each provider with the redirect address, scopes, `gcloud` command and Slack app manifest ready to copy, a form for the client ID and secret, and a **Check** button that says what is still wrong (unknown client, redirect address not registered, http where https is needed).

The client ID and secret can be saved on that page (encrypted with the saved keys) or set in the environment, which wins: `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`, `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET`. "Sign in with Google / GitHub" on the login page still reads the environment only.

The redirect addresses are `<base>/api/v1/auth/oauth/<provider>/callback`, where `<base>` is `COGNITRIX_PUBLIC_URL`, or `http://127.0.0.1:48210` in the desktop app.

- **GitHub** uses the hosted server at `https://api.githubcopilot.com/mcp/` with the scopes `repo read:org read:user`. Organizations may have to approve the OAuth app. Anyone can paste a personal access token instead.
- **Google** uses Google's Workspace MCP servers (developer preview), for example `https://gmailmcp.googleapis.com/mcp/v1`. Each app needs its normal API and its MCP API turned on, the scopes added to the consent screen, and a "Web application" client with "Use this client for an AI-powered agent" ticked.
- **Slack** uses Slack's server at `https://mcp.slack.com/mcp` with a user token. Slack only lets apps internal to a workspace (or in its Marketplace) use it, and only accepts https redirect addresses.

**Notion, Linear, Sentry and servers added by address** need no setup. Cognitrix reads the server's sign-in metadata and registers itself, with a client ID metadata document when the server has a public https address, or dynamic client registration otherwise. On a shared server, every address in that metadata must be public https.

Each connected app has a **Test** button (`POST /api/v1/mcp/apps/{id}/test`). It checks the sign-in, opens the app's server, counts its tools and checks each extra permission.

## More apps with Composio (optional)

A person can add their own Composio API key under Connected apps to reach apps that have no MCP server of their own. It is off until a key is added, and there is no relay: Cognitrix calls Composio's REST API (`cognitrix/mcp/composio.py`, `COMPOSIO_BASE_URL` to override the address) with that person's key.

- The key is kept encrypted in a hidden row (`catalog_id = 'composio'`). Each connected app is a row with `catalog_id = 'composio:<slug>'` and `auth_type = 'composio'`, so agents pick it on their Connections tab and use it through the same two tools, with the same Allow, Ask and Off settings.
- Sign-ins and tokens for these apps are held by Composio, and tool calls pass through its servers. The page says so before the key is saved.
- Only apps Composio can sign in to on its own are offered. Others need their own OAuth app set up in the Composio dashboard.

## Starting a task from new mail

A task's Automation panel can add a **new Gmail** trigger next to webhooks (`TaskTrigger` of kind `gmail`, `cognitrix/tasks/mail_triggers.py`). The scheduler asks Gmail's history API what arrived in the inbox since the last look, every `COGNITRIX_MAIL_CHECK_SECONDS` (default 120, minimum 30), and queues one event per check. The event is handled like a webhook: one run, with the mail passed as untrusted data.

- Only the sender, subject, date and Gmail's short snippet of up to 10 messages go into the event. The agent reads the rest with its Gmail tools.
- An optional filter keeps only mail whose sender or subject contains some text.
- Mail that was already there when the trigger was added, or while it was off, never starts a run.
- It needs the owner's Gmail connection and the Gmail API turned on in the Google Cloud project. A failed check shows its reason on the trigger.

## Limits

- Pending sign-ins are kept in memory for ten minutes, like "Sign in with Google". Run one API process, or keep a sign-in on the process that started it.
- Google Calendar's MCP server is read-only today; changing events goes through the built-in tools above.
- New mail is found by asking on a timer, not pushed, so a run starts up to a couple of minutes after the mail arrives.
