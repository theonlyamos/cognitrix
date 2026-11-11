# Background computer use

Agents can work in app windows **without taking over the person's screen**: no
mouse movement, no keyboard input to whatever the person is typing in, and the
foreground window stays where it is. The driver lives in `cognitrix/computer/`
(platform-neutral logic in `__init__.py`, one backend per system: `windows.py`,
`linux.py`, `macos.py`); the agent tools are in `cognitrix/tools/computer.py`.

Windows and Linux (X11 sessions) are supported, macOS is experimental and off by
default. Elsewhere the tools say so and the
full-screen tools (`take_screenshot`, `mouse_click`, `text_input`, ...) still work.
Most of this page describes Windows; see [Linux](#linux) for what differs.

## Tools

| Tool | What it does | Asks first? |
|------|--------------|-------------|
| `list_windows(search?)` | Open app windows with their ids; says which can't be controlled and why | no |
| `open_app(target)` | Opens an app, file or folder without taking focus; returns the new window | yes (it can start any program) |
| `look_at_window(window_id, find?, max_elements=60)` | Screenshot of that window (attached as an image, even when covered) plus numbered controls | no |
| `click_element(window_id, element, action="click")` | Press/open (`click`), `select`, `toggle` or `expand` a control | yes |
| `set_text(window_id, text, element?)` | Put text into a field (replaces its contents) | yes |
| `click_in_window(window_id, x, y, button="left", double=false)` | Click a point in the window screenshot | yes |
| `type_in_window(window_id, text, x?, y?)` | Type at the focused field (optionally click it first) | yes |
| `press_key_in_window(window_id, keys)` | A key (`enter`, `tab`, `down`, ...) or a shortcut (`ctrl+s`) | yes |
| `scroll_window(window_id, direction="down", amount=3, element?)` | Scroll the window or one control | no |

"Asks first" follows the approval policy in `cognitrix/safety/destructive_ops.py`:
looking and scrolling are harmless; opening an app, and anything that presses,
types or changes text can press "Send" in any app, so it asks like the
full-screen desktop tools do (and "remember for this chat" applies).

### Answering once per app

The approval card for a click, typing, key press or `set_text` names the app
and adds two buttons: **allow <App> in this chat** and **always allow <App>**.
"Always" is saved for the person (not the agent) and shows up under
Settings > Agent access > Apps agents can use, where each app is Always allow,
Ask or Never. "Never" refuses the action without asking. The CLI prompt has
the same choice (`a`).

An action with `use_screen=true` takes over the real mouse and keyboard for a
moment, so it is asked every time: no "approve for session", saved answer,
chat auto-approve or reviewer stands in for the person.

An app answer never covers `open_app`, an action that takes over the screen,
or an agent whose own level or tool setting says to ask or block. Code:
`cognitrix/computer/apps.py`, `GET`/`PUT /computer/apps`.

Coordinates are pixels of the window screenshot the model was shown (it is
scaled to fit 1280x800), or a 0-1000 grid for Gemini, the same as `take_screenshot`.

## Every action is verified

Each action reads state back and returns one of:

- **confirmed**: the effect was seen (a value, toggle/selection/expanded state, title or the control tree changed)
- **unverified**: the action was delivered but nothing visible changed; the model is told to look again
- **needs_screen**: this target can't take the action in the background (see below)
- **failed**: the action didn't land, e.g. typed text is missing from the field

Results also say when the foreground window changed during the action
(`focus_changed` is logged for every action).

## How it works

- **Windows list**: `EnumWindows`, skipping hidden, cloaked, tool and shell windows.
  UWP apps hosted by `ApplicationFrameHost` are resolved to the real app.
- **Opening**: `ShellExecute` with `SW_SHOWNOACTIVATE`; the new window is found by
  diffing the window list (launch pids are wrong for Explorer and UWP apps).
- **Screenshots**: `PrintWindow(PW_RENDERFULLCONTENT)` cropped to the DWM frame.
  Works when the window is covered. A minimized window is restored without
  activation and sent behind other windows first. An all-black capture is
  reported instead of being shown.
- **Opening files**: the file's associated program is started directly with the
  file (packaged apps through their execution alias). Opening through the shell
  association lets the app pull itself to the front.
- **Controls**: UI Automation (via `comtypes`) with one cached `FindAll` per look.
  Numbers belong to a snapshot; a newer look of the same window makes older
  numbers stale, and a control that has gone or changed role is refused.
  Chromium/Electron build the page's tree only when asked, so a look without a
  page document sends `WM_GETOBJECT` and reads again (a freshly loaded page can
  take a few seconds).
- **Actions**: UIA patterns first (Invoke, Toggle, SelectionItem, ExpandCollapse,
  Value, Scroll). A pixel click uses the smallest control under the point when it
  has a pattern, otherwise it posts mouse messages to the deepest child window.
  In Chromium/Electron, clicks are always posted mouse messages: their
  accessibility actions pull the window to the front.
- **Keys**: posted `WM_KEYDOWN/UP` to the app's focused child. Shortcuts first look
  for a control with that accelerator; otherwise the key is posted while the
  modifier is marked down in the *app's own thread* key state
  (`AttachThreadInput` + `SetKeyboardState`). The person's keyboard isn't touched,
  and this is never done to the app in the foreground.
- **Focus**: if an app pulls itself to the front during an action (WinUI menus,
  some Chromium patterns) or just after opening, focus is handed straight back to
  the person's window and the result says so.
- **Locks**: one action per window at a time (other windows can be driven in
  parallel); within one agent turn the desktop tools already run one by one.
- **Overlay hook**: `Driver.add_listener(callback)` receives an `ActionEvent`
  (window id, action, screen x/y) before each action, for a future on-screen agent cursor.

## What works in the background

Tested live on Windows 11 (see `tests/test_background_computer_use_live.py`):

- Calculator: 12 × 34 = 408 by controls, and a pixel click on a button.
- Notepad: `set_text` then Ctrl+S saves to disk; typing after clicking the editor;
  Ctrl+W closes a tab.
- File Explorer: open a folder, `select` an item without opening it, rename by
  setting its Name cell, minimize then look (restored behind other windows).
- A window covered by others is captured correctly.
- Brave (Chromium) in a throwaway profile on Wikipedia: link by control, link by
  pixel and the Back button all confirmed without any focus change; setting the
  address bar works.

## What needs the screen

- **Typing and keys in Chromium/Electron apps** (browsers, VS Code, Slack) and
  most games/canvas apps: they ignore posted keystrokes, so for example a typed URL
  can't be submitted with Enter. The tools say `needs_screen` right away and point to
  `use_screen=true` (below). Clicking links and buttons still works in the background.
- **Scrolling and setting fields in Chromium** go through accessibility patterns,
  which briefly pull the window forward; focus is handed back and the result says so.
- **Opening modern (WinUI) menus**, e.g. Notepad's File menu, activates the app, and
  handing focus back closes the menu again. Use the shortcut (Ctrl+S, Ctrl+W, ...) instead.
- **Posted typing in some apps** depends on which control the app thinks has
  focus. It is verified; when it doesn't land the result is `failed`, and
  clicking the field first (pass `x`, `y`) or `set_text` fixes it.
- **Notepad saves after `set_text`** aren't visible in the window (it doesn't mark
  the tab as modified), so Ctrl+S reports `unverified` though the file is written.

## Using the screen for a moment (`use_screen`)

`click_element`, `click_in_window`, `type_in_window` and `press_key_in_window` take
`use_screen=true` for the cases above, so the agent never has to switch to the
full-screen tools for one window. The driver then:

1. waits until the person has not touched the keyboard or mouse for a second
   (up to 6 s; otherwise it doesn't take the screen at all),
2. brings only that window to the front (after a zero-distance mouse move, since
   Windows lets only the process behind the latest input take the foreground),
3. clicks or types with real input (`SendInput`; typing at 30 ms per character,
   because faster typing loses characters to browser autocomplete), checking before
   every character that the window is still in front, and stopping if the person
   switched windows,
4. puts the pointer and the person's foreground window back, and verifies the effect
   (text read back from the field where possible).

Every `use_screen=true` call is classified as `screen_takeover` (see
`cognitrix/safety/destructive_ops.py`): it asks the person, and a per-app
"always allow" never covers it. Tested live in a throwaway Brave window: typing
into Wikipedia's search box and submitting it, typing a URL and Enter in the address
bar, and Alt+Left.

## Never controlled

Terminals and consoles (use the shell tool), Cognitrix's own windows, known
password managers, Windows security prompts, and apps running as administrator
(Windows blocks input to them from normal apps anyway).

## Linux

`cognitrix/computer/linux.py` uses two system libraries through ctypes, so
nothing extra is installed: `libatspi` (the accessibility bus) and `libX11`.

- **Needs** an X11 session with the accessibility bus running (it is on GNOME,
  KDE, Xfce, Cinnamon and MATE by default). **Wayland is not supported**: it
  doesn't let one app list, read or send input to another app's window, so the
  tools say so and the full-screen tools are the way there.
- **Controls** are read and used through AT-SPI: buttons, check boxes, radio
  buttons, tabs, menu items (including ones in closed menus, with their
  shortcuts), text fields, sliders and scroll bars. None of it brings the
  window forward. GTK and Qt apps answer; Chromium and Electron only when
  started with `--force-renderer-accessibility`.
- **Typing** goes into the field at its caret through AT-SPI, after
  `click_in_window`/`type_in_window(x, y)` put the caret in it. A shortcut
  such as `ctrl+s` runs the menu item that carries it.
- **Screenshots** come from the Composite extension, so a covered or partly
  off-screen window still reads correctly. A minimized window has no picture
  (restoring it would bring it forward) but its controls still work.
- **Sent clicks, keys and wheel turns** (`XSendEvent`) are ignored by GTK 3+,
  Qt 5+ and Chromium, and accepted by older toolkits. So a click on a spot
  with no control, a bare key such as `tab`, and wheel scrolling usually come
  back `unverified` on modern apps: use the controls instead.
- **Never controlled**: terminals, password managers, password and
  authentication prompts, Cognitrix's own windows, and windows of another
  user (for example root).

Live test (opens GTK's widget demo; works on a virtual display):

```sh
COGNITRIX_LIVE_DESKTOP=1 pytest tests/test_background_computer_use_linux_live.py
```

## macOS (experimental)

`cognitrix/computer/macos.py` is written from Apple's documentation and **has
not been run on a Mac yet**, so it stays off until `COGNITRIX_MAC_BACKGROUND=1`
is set. It reads and uses controls and menu-bar items through the Accessibility
API, captures one window (also when covered), and sends clicks and keys to one
app with `CGEventPostToPid`, which leaves the real pointer alone. It needs the
Accessibility permission, and Screen Recording for pictures. Treat the first
run on a Mac as a test run.

## Running the live tests

```powershell
$env:COGNITRIX_LIVE_DESKTOP = '1'
pytest tests/test_background_computer_use_live.py
```

They open and close their own Notepad, Calculator, Explorer and browser windows
(the browser with a throwaway profile), skip an app that is already open rather
than touching someone else's window, and remove their own Notepad tabs so
Notepad's restored session keeps only the person's tabs. Moving the mouse or
switching windows while they run can make the focus checks fail.
