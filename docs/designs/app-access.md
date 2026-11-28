# What an app's server code may use

Apps (agent-built web apps, `cognitrix/hosted_apps`) have two ways to reach the
owner's connected apps, saved sign-ins and keys.

| | Who is asked | Acts as | Where the code runs |
|---|---|---|---|
| `window.cognitrix.apps` (`bridge.py`) | whoever is looking, the first time, and before every change | the viewer | the app's page |
| `/api/v1/app-access` (`access.py`) | the owner, once, on the app's page under **Access** | the owner | the app's server |

This page is about the second one, for work with nobody looking: a nightly
sync, a webhook, a form that files a ticket.

## What the owner can turn on

- **Connected apps** (MCP servers, signed-in apps, Composio), one by one:
  *Read only* runs tools that change nothing; *Read and change* runs the rest
  too, with nobody asked each time.
- **Saved sign-ins** (Settings, Saved sign-ins). The app never gets the value.
  It asks Cognitrix to send the request (`POST /api/v1/app-access/http`) and
  names the sign-in in `credential` or writes `{{secret:NAME}}` where the value
  goes. The rules are the Http Request tool's, from the same code
  (`tools/http.py: send_signed`): only the sign-in's own sites, never across a
  redirect to another host, private addresses refused, echoed values replaced
  with `[NAME hidden]`. A request with no sign-in in it is refused, so this is
  not a general proxy.
- **Secrets for this app**: values only this app needs. They are encrypted at
  rest (`secret_box`) and handed to the app as environment variables when a
  version starts, so a change needs a restart (the tab offers one). Use a saved
  sign-in instead when the app should never see the value.

Everything is off until the owner turns it on. The owner's page never gets a
value back, only names and the last four characters of a secret.

## How the app calls it

An app that asked for (or was given) a connected app or a sign-in is started
with `COGNITRIX_API_URL` and `COGNITRIX_API_KEY`. The key is made fresh for
each version and the old one is revoked once the new version is live. Unless
the app was published with `use_cognitrix_api`, the key has no scopes: it opens
`/api/v1/app-access` and nothing that needs `read`, `write`, `chat` or `run`.

    GET  /api/v1/app-access              what is allowed now, what is waiting
    GET  /api/v1/app-access/apps         the allowed connected apps and tools
    POST /api/v1/app-access/apps/call    {app, tool, arguments}
    POST /api/v1/app-access/http         {url, method, headers, body | json, credential}

Each call is checked against the owner's choices as they are at that moment,
so taking something away works at once, with no restart. Each use is written to
the audit log under the owner.

## Who decides

- The agent that builds an app can only **ask**: Publish App `connected_apps`,
  `credentials`, `secrets`. Asks show on the Access tab ("asked for") and the
  tool result tells the agent what is still waiting, to pass on.
- Only the owner can allow anything, from Cognitrix's own page
  (`PUT /hosted-apps/{id}/access`, `/secrets`, browser session only). People
  the app is shared with for editing can't see or change the tab.
- When someone other than the owner publishes a version, everything here is
  **paused**: that version starts without the secrets and its calls answer 403
  until the owner turns access back on. Otherwise an editor's code would act as
  the owner.

## Not covered

- Rolling back to an older version (Make live) starts it with that version's
  old, revoked key. Restart from the Access tab to give it a working one.
- Secrets are visible to the app's code and sit in the runner's start file, as
  the app's API key always has. They are not scrubbed from agent tool output
  the way saved sign-ins are.

## Visitors from the owner's tailnet

An App can also be shared with people on the owner's Tailscale network who have no account here (the Share dialog's "Your tailnet" section; `store.set_sharing(tailnet=...)`). Nothing in this document changes for them: what the App's own server code may use is still decided by its owner on the Access tab, and a guest has no connected apps or saved sign-ins of their own to offer. The page bridge (`hosted_apps/bridge.py`) marks them (`window.cognitrix.guest`) and rejects every call that would ask a person for something. The App's server sees `X-Cognitrix-User-Source: tailscale`. See [Tailscale](../tailscale.md#apps-for-people-on-your-tailnet).
