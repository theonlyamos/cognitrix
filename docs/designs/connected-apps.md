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

## Watching a GitHub pull request

Every GitHub connection (signed in or with a token) has three built-in tools next to GitHub's own: **Watch Pull Request**, **Stop Watching Pull Request** and **List Watched Pull Requests** (`cognitrix/mcp/native.py`, logic in `cognitrix/tasks/pr_watch.py`). An agent calls Watch Pull Request after opening a pull request, or when asked to keep an eye on one, and is woken when something happens instead of checking over and over.

- **Where news goes.** In a chat, news arrives in that chat as a new message and starts a turn of the agent (the same path as replies from chats an agent started). In a task run, the watch adds a "github" trigger to the task and news starts a new run, with the news as untrusted data and the agent's own note on what to do.
- **What counts as news.** A new review or comment, a review comment, CI results, ready for review, merged, closed, reopened. New commits are mentioned but don't wake the agent alone.
- **CI.** One summary per run of checks on the head commit, once every check has finished ("Checks finished on abc1234: 2 failed (...), 5 passed." or "All 7 checks passed"). If a check fails while others keep running for more than 15 minutes, the failure is told early, once.
- **Bundling.** News waits one minute for more before it is delivered, so a review with ten comments wakes the agent once. Merged and closed are delivered right away.
- **The agent's own comments.** Every write an agent makes through a GitHub connection is noted (`GitHubAgentWrite`, kept two days), with the comment and review ids found in the tool's answer. A comment or review from the connected account with one of those ids, or made within 30 seconds of such a write when the answer had no id, is the agent's own and skipped. The person writing as themselves still wakes the agent.
- **Stopping.** Watching stops when the pull request is merged or closed (that last bit of news is still delivered), after 30 days without news, when its chat or task is deleted, or from Automations › Watched pull requests. A person can have 20 watches at once. Wake-ups of a task count against its daily automation cap; a chat stops being woken after several agent reports in a row without the person, as for other agent reports.

- **Pull requests agents open are watched without being asked** (`cognitrix/tasks/pr_autowatch.py`). After every tool call in a chat or task run, the agent loop (`Agent.call_tools`) looks for a pull request the call opened: a GitHub "create pull request" tool (also through Run Mcp Tool), or a create command or message (`gh pr create`, `hub pull-request`, "Created pull request ...") next to a github.com pull request link, in Bash, Bash Output (so Coding Agent), Run Code, the sandbox or a paired PC. Found by a command, the pull request must have been opened in the last 30 minutes, so one that is only looked up or listed isn't watched. The watch is like one from Watch Pull Request (news to the same chat or a run of the same task, stops on merge or close), marked `auto`, with instructions to act on reviews and failing checks, and the tool's result tells the agent. One watch per pull request: none is added when it is already watched anywhere. It is on by default: switch it off for the account (Tasks › Watched pull requests, `GET/PUT /github/watches/settings`) or for one project (project settings, `watch_prs`). Watches started in a project carry its `project_id` and are listed on the project's Pull requests tab (`GET /projects/{id}/pull-requests`).

**Desktop app (and servers GitHub can't reach):** the scheduler checks each watched pull request every `COGNITRIX_GITHUB_CHECK_SECONDS` (default 120, minimum 30) while checks are running or there was activity in the last hour, five times less often after an hour of quiet and fifteen times less often after a day (at most every 30 minutes). Each check asks with the ETags of the last answers, so an unchanged pull request costs three "304 Not Modified" answers, which don't count against GitHub's rate limit. Reviews and comments are only read when the pull request's own "updated" time moved. Failed checks back off (doubling, up to the slow interval); when GitHub's rate limit is reached, checks wait for its reset.

**Web server:** when `COGNITRIX_PUBLIC_URL` is an https address (and not the desktop app), the first watch on a repository adds one webhook to it for that GitHub connection, posting to `/api/v1/github/hooks/<id>` with its own secret. No tunnel is needed. Deliveries are checked against `X-Hub-Signature-256`, may be up to 2 MB, and are filtered by `X-GitHub-Event` and pull request number (or head commit for check and status events); a matching delivery makes the check run right away. A timed check still runs every 15 minutes in case a delivery is missed. The webhook is removed when the last watch using it stops. Adding a webhook needs admin rights on the repository; the `repo` scope the GitHub app already asks for covers it. When GitHub refuses, the watch falls back to timed checks and says so. Set `COGNITRIX_GITHUB_WEBHOOKS=0` to always use timed checks.

**Web server with a GitHub App (optional, recommended):** a GitHub App gets events from every repository it is installed on, without admin rights and without one webhook per repository. When the App is installed on a watched repository, the watch uses it instead of a repository webhook; otherwise it falls back as above. To set one up:

1. GitHub › Settings › Developer settings › GitHub Apps › New GitHub App. Webhook URL: `<COGNITRIX_PUBLIC_URL>/api/v1/github/app/webhook`, with a webhook secret.
2. Repository permissions, read-only: Pull requests, Issues, Checks, Commit statuses, Metadata. Subscribe to events: Pull request, Pull request review, Pull request review comment, Issue comment, Check run, Check suite, Status.
3. Generate a private key, and set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (the PEM text, with `\n` for line breaks, or a path to the .pem file) and `GITHUB_APP_WEBHOOK_SECRET`.
4. People install the App on their repositories (or organization). The App only says that something happened; the check still reads the pull request with the person's own GitHub connection, so they see nothing they couldn't already see.

## Limits

- Pending sign-ins are kept in memory for ten minutes, like "Sign in with Google". Run one API process, or keep a sign-in on the process that started it.
- Google Calendar's MCP server is read-only today; changing events goes through the built-in tools above.
- New mail is found by asking on a timer, not pushed, so a run starts up to a couple of minutes after the mail arrives.

## App screens beside the chat (MCP Apps)

A connected app can give a tool its own screen (the MCP Apps extension,
`io.modelcontextprotocol/ui`, which Cognitrix advertises when it connects).
The tool's `_meta.ui.resourceUri` names a `ui://` HTML resource.

- When an agent runs such a tool in someone's chat, `cognitrix/mcp/app_views.py`
  keeps an `AppView` (app, screen, tool arguments and result) and the result
  text starts with `... is showing this beside the chat (app_view <id>, version <n>)`.
  The chat opens it in the right-hand panel (`frontend/src/components/appviews/AppViewPanel.tsx`).
- Running a tool with the same screen again in the same chat updates that view;
  the open screen gets the new `tool-input` / `tool-result` without reloading.
- The screen is served from `/api/v1/app-view-frame/<token>` with a CSP built
  only from the domains the app declared (`_meta.ui.csp`), plus `sandbox`
  without `allow-same-origin`, so it has an opaque origin. Iframe permissions
  (camera, microphone...) are not granted.
- The screen may call tools of its own app only (`/api/v1/app-views/<id>/call`).
  The agent's Allow / Ask / Off levels apply. With no level set, tools made only
  for the screen (`visibility: ["app"]`) and read-only tools run on a click;
  others that change things, and anything destructive, ask the person first.
- Tools with `visibility: ["app"]` are never offered to agents.
- `ui/message` sends a chat message as the person; `ui/update-model-context`
  is added to the agent's next turn in that chat (kept in memory, not saved).

Try it with `tests/fixtures/mcp_apps_demo.py` (a task board), added as a stdio server.
