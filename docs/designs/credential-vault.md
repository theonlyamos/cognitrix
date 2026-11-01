# Saved sign-ins (credential vault)

Agents can call APIs and connected apps that need a login without the token or
password ever entering the model's context.

## How it works

1. A person saves a credential in **Settings > Saved sign-ins**: a name
   (`GITHUB_TOKEN`), a kind (token or username + password), the hosts it may be
   sent to (`api.github.com`, `*.atlassian.net`), an optional header name and a
   description. The value is encrypted at rest and never returned by the API.
2. The model sees names only: the **List Credentials** tool returns name, kind,
   hosts and description.
3. To use one, the model either names it in the **Http Request** tool's
   `credential` field (adds `Authorization: Bearer …`, Basic auth, or the chosen
   header) or writes `{{secret:NAME}}` / `{{secret:NAME.username}}` /
   `{{secret:NAME.password}}` in the URL, a header or the body.
4. The value is filled in right before the request is sent, and only if the
   request goes to one of the credential's hosts. Otherwise the call is refused
   before anything is sent. A redirect to another host is not followed once a
   credential is attached. Placeholders cannot be part of the host name.
5. Every tool result (any tool: HTTP, MCP, Bash, Read…) is scrubbed of the
   person's saved values before the model sees it. An echoed token shows up as
   `[GITHUB_TOKEN hidden]`.

Chat history, approval cards and logs keep the placeholder, never the value.
The approval policy is unchanged: GET requests run; POST/PUT/PATCH/DELETE ask.

## MCP servers

Header values of a person's own MCP server can be `{{secret:NAME}}`. The value
is filled in only for the live connection (`registry.ensure_connected`), only
from the server owner's sign-ins, and only if the server's host is allowed.
Shared servers have no owner and can't use anyone's sign-ins.

## Encryption at rest

`cognitrix/common/secret_box.py` seals values with Fernet (AES + HMAC), stored
as `enc:v1:…`. Covered: saved sign-ins, MCP server headers and env, pasted voice
keys. Plain-text rows from earlier versions are sealed on startup.

Key: `COGNITRIX_SECRETS_KEY`; otherwise `~/.cognitrix/secrets.key` (created
0600) outside production; in production a key derived from `JWT_SECRET_KEY`
(with a warning). `COGNITRIX_SECRETS_OLD_KEYS` keeps old keys readable for
rotation.

## Fit with connected-app sign-in (OAuth)

Kind `oauth` is reserved: `{"access_token", "refresh_token", "expires_at",
"token_url", "client_id"}`. The planned connector sign-in (plugins phase 2)
writes an `oauth` credential for the person and points the MCP server's
`Authorization` header at `Bearer {{secret:NAME}}`; refresh then only has to
update the credential row. Nothing in the model path changes.

## Not covered yet

- Bash: secrets are not injected into commands (output is still scrubbed).
- Browser logins (filling a login form): Cognitrix has no browser tool yet.
- Agent provider API keys on agent rows are still stored as before.
