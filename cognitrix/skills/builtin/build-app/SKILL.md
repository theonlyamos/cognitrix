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
version: "1.3.0"
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
   - People can install an app on their computer (desktop app). In the browser code, `window.cognitrix`
     can then ask them for more; each call asks the person the first time and rejects if they say no,
     so always handle a rejected promise:
     `cognitrix.notify(title, body)` shows a system notification (good for reminders and finished work);
     `cognitrix.tray.set({tooltip, title, items: [{id, label}]})` and `cognitrix.tray.onClick(id => ...)`
     give an installed app its own tray (menu bar) icon and menu;
     `cognitrix.apps.list()` and `cognitrix.apps.call(app, tool, args)` use the person's connected apps
     (Gmail, Calendar, ...) as them: reads run, changes ask the person each time.
     Use these only when the user wants that; they're all optional.
   - Don't commit `node_modules` or `.venv`; install them with the install command.
4. **Make its icon. Every app needs one.** It shows on the Apps page and becomes the shortcut icon
   (Start menu, Dock, desktop) when someone installs the app. Save a square `icon.svg` in the app's
   folder (Publish App picks up `icon.svg` or `icon.png` by itself; pass `icon` for another file):
   - `viewBox="0 0 256 256"`, a full-bleed rounded tile (`<rect width="256" height="256" rx="56">`) in
     a colour that suits the app, and one bold, simple symbol of what the app does (a checkmark for a
     todo list, bars for a dashboard), centred, about half the tile. No text beyond one or two letters,
     no thin lines or tiny details: it must read at 16 px. No scripts or event handlers.
   - Or, for a picture-style icon, use `Generate Image` with `aspect_ratio: "1:1"` and
     `save_to: "~/.cognitrix/apps/<name>/icon.png"` (a flat app icon on a plain background, no text).
   - Link it as the page's favicon too: `<link rel="icon" href="icon.svg">` (relative).
   Without one the app shows a placeholder with its initials, and Publish App will remind you.
5. **Try it** if you can run commands: start it with `PORT=8765 APP_DATA_DIR=./.data` and fetch a page.
   Skip this when the shell isn't available; publishing reports start errors with the log.
6. **Publish** with `Publish App`: `title` (2-5 words), `folder` = the app's folder, `start_command`
   (e.g. `python3 app.py`, `node server.js`), `install_command` when needed (e.g.
   `pip install -r requirements.txt`, `npm ci`), a one-line `note`, and `description`.
   Only set `use_cognitrix_api` when the app itself must run agents or read tasks.
7. **If it fails**, read the log in the result (or `App Status`), fix the code and publish again with the
   same `app_id`. The previous live version keeps serving meanwhile.
8. **Look at it** with `Check App` (its `app_id`): you get a screenshot plus script errors, console errors
   and failed requests. Check the main pages (`path`) and once with `phone: true`. Fix anything broken
   or ugly, publish again and check again.
9. **Click through it** with `Test App`: write the steps a person would take for each thing the app is
   for, and check the result after each action. For a todo list, for example:
   `[{"fill": "Title", "with": "Buy milk"}, {"click": "Add"}, {"see": "Buy milk"},
   {"reload": true}, {"see": "Buy milk"}, {"click": "Delete"}, {"not_see": "Buy milk"}]`.
   - Each run starts a throwaway copy of the app with **empty data**, so write steps that create what
     they need first. Use `data: "copy"` only to test against a copy of the real data. Test copies
     get no Cognitrix API key (`APP_TEST=1` is set), so flows that run agents can't be clicked through.
   - Target things by what people see: button text, field labels, placeholders. Use a CSS selector
     (`#list li`) to count rows or when the text is ambiguous. If you control the markup, give
     inputs a `<label>` and buttons clear text; that is good for people and makes tests readable.
   - Cover the main flows (create, edit, delete, any filter or search), a reload to prove data is
     saved, and the empty state. Run the most important flow once with `phone: true`.
   - When a step fails, read why (the page text and the buttons and fields listed at that point, the
     screenshot, the app log). Fix the app, publish again and re-run. Fix the step instead only when it
     targeted the wrong thing.
   - Give each test a `name` and `save: true`: once it passes it is kept in the app's
     `app-tests.json` (you can also edit that file directly).
   Don't tell the user it's done while Check App shows errors or a test fails.
10. **Tell the user** in two or three sentences what the app does, which flows you tested, and that the
   card in the chat opens it. It is private to them until they share it.

## Updating later

Edit the files in the same folder (keep `icon.svg`; make one if the app has none), publish again with the app's `app_id` and a `note` saying what
changed, then run `Check App` and `Test App` with no steps (it re-runs every saved test). Add a saved test
for what you changed. Never start a new app for a change to an existing one.
