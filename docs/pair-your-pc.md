# Pair your own PC

A Cognitrix web account can use the person's own computer as a second kind of
computer for agents, next to the sandbox. The sandbox stays the default; a chat
works on a PC only when the person picks it with the computer chip in the chat
box.

## Pairing

1. On the web: **Settings › Computers › Pair a computer**. This shows a
   one-time code (`XXXX-XXXX`, 10 minutes, used once).
2. On the PC, either:
   - the desktop app: click **Open in the desktop app** in the pairing dialog
     (or go to **Settings › Computers › This PC** and enter the web address and
     the code), check the account it shows, pick folders and what agents may
     do, then **Allow**; or
   - a terminal: `cognitrix connect https://your-server --code XXXX-XXXX
     --folder ~/code` (repeat `--folder`; `--read-only`, `--no-commands`,
     `--apps`, `--pc-name`; `--status` and `--unpair` later).

The PC keeps its token in the system keychain: Windows Credential Manager, the
macOS Keychain, or the Secret Service on Linux (when `secret-tool` is
installed). Without one it goes in `~/.cognitrix/paired.json` (mode 600; set
`COGNITRIX_PAIRED_CONFIG` to move the file, `COGNITRIX_PAIRED_KEYCHAIN=0` to
skip the keychain). The server only stores its hash. A PC not seen for 30 days
must pair again.

## Over Tailscale

When the server has a Tailscale private address ([Tailscale](tailscale.md)) and the PC is on the same tailnet:

- **Find my server.** In the desktop app, **Settings › Computers › This PC › Find it on my tailnet**, or `cognitrix connect --find`. The PC asks each machine on its tailnet one small question (`GET /api/v1/computer/devices/hello`, which answers with the app's name and version) and lists the ones that are Cognitrix.
- **Pair without a code.** If your Tailscale login is linked to your account (Settings › Sign-ins on the server, with "Sign in from the tailnet" turned on), the PC can ask instead: **Pair without a code** in the desktop app, or `cognitrix connect <server> --ask --folder ~/code`. The request shows on the web under **Settings › Computers** with the PC's name, its machine name on the tailnet and the Tailscale login. Press **Approve**, and the PC picks its folders and gets its token as usual. A request lasts 10 minutes, a new one from the same machine replaces the old one, and a device that itself signed in with a code in the last day cannot approve one. An approval stops counting if the device that gave it is signed out, or the Tailscale login is unlinked, before the PC finishes. Without a linked login, pair with a code.
- **Linking happens at the PC.** In the desktop app, linking the PC, choosing its folders and unlinking it only work in the app's own window on that PC. A session that reaches the app from another machine (its Tailscale address, the local network) can look at the link, not change it, and a pairing request belongs to the session that started it.
- **Plain http inside the tailnet.** `http://` addresses are refused, except for a machine that Tailscale on the PC lists in its own tailnet (by its tailnet IP, or a name that resolves only to one). Tailscale already encrypts that path. This is checked every time the PC connects.
- **Two addresses, one link.** At pairing the server tells the PC where else it answers (its Tailscale address and its public address). The PC keeps them all and tries the Tailscale one first, so the link survives one of them going away. Before the PC uses an address the server named, that address has to show it is the same server (`POST /api/v1/computer/devices/prove`: it answers a fresh question with something only the paired server can work out, and only for addresses it answers at today). An address that can't is skipped: it never receives the PC's token and never gets to send the PC work. The address you paired at needs no such check.

## How it works

The PC dials out over a WebSocket to `/api/v1/computer/devices/connect`; no
ports are opened on the PC. When a chat works on a PC, Bash, Bash Output, Kill
Shell, Read, Write, Edit, Glob and Grep are sent to it and run there, inside
the shared folders only. The PC enforces its own folder list, read-only
folders, the files/commands switches and pause, whatever the server asks.
Each command asks the person in the chat first. Commands start only in folders shared read & write and run as the person's own user, so a command can reach whatever that user can: the approval is the guard, not the folder list. Helper agents a chat calls can only Read, Glob and Grep on the PC. The PC also keeps a local log
of what ran in `~/.cognitrix/paired-log.jsonl`.

## What a PC can allow

- **Files**: Read, Write, Edit, Glob and Grep in the shared folders.
- **Commands**: Bash, in read & write folders only, each one approved in the chat.
- **Apps**: the window tools work on the PC's own screen, and the chat's side
  panel shows the screen. Opening an app always asks the person first.
- **Send tasks**: the desktop app can hand a task to the web account (on the
  task page, **Run on** the web account) and follows its result under
  **Settings › Computers › This PC**.

Scheduled tasks can run on a PC too (the **Computer** field on the task). The
task worker reaches the PC through the web process (`paired-relay.json` in the
work folder, or `COGNITRIX_INTERNAL_URL`). If the PC is offline, the task waits
up to 30 minutes (`COGNITRIX_PAIRED_TASK_WAIT_S`), then fails and notifies the
person.

## Undo

Before an agent's Write or Edit changes a file, the PC keeps a copy of the file
as it was before that chat first touched it (`~/.cognitrix/paired-undo/`, kept
14 days, files up to 20 MB). The chat's side panel lists the changed files with
**Undo** for each and **Undo all**. Changes made by commands aren't tracked.

## Limits in this version

- If the PC is offline a chat's tool fails right away with a message telling
  the person to start the desktop app or `cognitrix connect`; the agent does
  not switch computers on its own.
- Running several web processes: PCs connect to one of them; chats served by
  another process see the PC as offline.
