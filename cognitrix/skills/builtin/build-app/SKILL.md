---
name: build-app
description: >
  Build and publish a live web app (internal tool, dashboard, form, tracker, review queue) that people open
  from a stable link. Use when the user asks for an app, a tool with its own data, or anything more than a
  single static page. The app's code goes in the apps folder (~/.cognitrix/apps/<name>).
context: same
args:
  - name: request
    description: What the app should do, in the user's words
    required: true
tags: [apps, web, build, publish, dashboard]
category: development
version: "1.0.0"
author: cognitrix
safety:
  risk-level: low
---

# Build and publish an app

The user wants: "$(arg request)"

## Where the code goes

Every app lives in its own subfolder of the **apps folder**, `~/.cognitrix/apps/` (your working-folders
list shows its full path). Pick a short lowercase name with dashes, e.g. `~/.cognitrix/apps/team-dashboard/`.
You can create and change files there without asking. Don't build apps anywhere else.

If an app with that name already exists (check with `App Status` and `Glob`), update it instead of making
a second one: edit its folder and publish again with its `app_id`.

## Steps

1. **Plan small.** Decide the pages, the data you store and the actions. One screen that works beats
   five that don't. If something important is unclear, ask one question; otherwise pick sensible defaults
   and say which you chose.
2. **Write the code** into `~/.cognitrix/apps/<name>/` with `Write` and `Edit`. Prefer the simplest stack:
   - Python standard library (`http.server` + `sqlite3`) or a single `app.py` with Flask/FastAPI plus a
     `requirements.txt`; or
   - Node.js with `server.js` (+ `package.json` when you need packages).
   - Plain HTML, CSS and JavaScript for the front end, no build step unless the user asked for one.
     If you use Vite or similar, set `base: './'` and build in the install command.
3. **Follow the app rules** (the app runs behind a path prefix in a sandboxed frame):
   - Listen on `127.0.0.1` at the port in the `PORT` environment variable.
   - Use **relative** URLs everywhere (`api/items`, `./static/app.js`), never ones starting with `/`.
   - Keep data (SQLite files, uploads) in the folder named by `APP_DATA_DIR`; it survives new versions.
     Everything else is replaced on each publish.
   - No cookies or localStorage. Who is viewing comes from the `X-Cognitrix-User-Name`, `-Email` and
     `-Id` request headers (percent-encoded UTF-8; decode them).
   - No websockets: poll instead.
   - Don't commit `node_modules` or `.venv`; install them with the install command.
4. **Try it** if you can run commands: start it with `PORT=8765 APP_DATA_DIR=./.data` and fetch a page.
   Skip this when the shell isn't available; publishing reports start errors with the log.
5. **Publish** with `Publish App`: `title` (2-5 words), `folder` = the app's folder, `start_command`
   (e.g. `python3 app.py`, `node server.js`), `install_command` when needed (e.g.
   `pip install -r requirements.txt`, `npm ci`), a one-line `note`, and `description`.
   Only set `use_cognitrix_api` when the app itself must run agents or read tasks.
6. **If it fails**, read the log in the result (or `App Status`), fix the code and publish again with the
   same `app_id`. The previous live version keeps serving meanwhile.
7. **Tell the user** in two or three sentences what the app does and that the card in the chat opens it.
   It is private to them until they share it.

## Updating later

Edit the files in the same folder and publish again with the app's `app_id` and a `note` saying what
changed. Never start a new app for a change to an existing one.
