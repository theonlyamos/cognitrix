# Pair your own PC

A Cognitrix web account can use the person's own computer as a second kind of
computer for agents, next to the sandbox. The sandbox stays the default; a chat
works on a PC only when the person picks it with the computer chip in the chat
box.

## Pairing

1. On the web: **Settings › Computers › Pair a computer**. This shows a
   one-time code (`XXXX-XXXX`, 10 minutes, used once).
2. On the PC, either:
   - the desktop app: **Settings › Computers › This PC**, enter the web address
     and the code, check the account it shows, pick folders and what agents may
     do, then **Allow**; or
   - a terminal: `cognitrix connect https://your-server --code XXXX-XXXX
     --folder ~/code` (repeat `--folder`; `--read-only`, `--no-commands`,
     `--pc-name`; `--status` and `--unpair` later).

The PC keeps a token in `~/.cognitrix/paired.json` (mode 600; set
`COGNITRIX_PAIRED_CONFIG` to move it). The server only stores its hash. A PC
not seen for 30 days must pair again.

## How it works

The PC dials out over a WebSocket to `/api/v1/computer/devices/connect`; no
ports are opened on the PC. When a chat works on a PC, Bash, Bash Output, Kill
Shell, Read, Write, Edit, Glob and Grep are sent to it and run there, inside
the shared folders only. The PC enforces its own folder list, read-only
folders, the files/commands switches and pause, whatever the server asks.
Each command asks the person in the chat first. Commands start only in folders shared read & write and run as the person's own user, so a command can reach whatever that user can: the approval is the guard, not the folder list. Helper agents a chat calls can only Read, Glob and Grep on the PC. The PC also keeps a local log
of what ran in `~/.cognitrix/paired-log.jsonl`.

## Limits in this version

- Files and commands only (no apps or screen).
- Chats only: scheduled tasks run by the worker can't reach a PC yet.
- If the PC is offline the tool fails right away with a message telling the
  person to start the desktop app or `cognitrix connect`; the agent does not
  switch computers on its own.
- Running several web processes: PCs connect to one of them; chats served by
  another process see the PC as offline.
