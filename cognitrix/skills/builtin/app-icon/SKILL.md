---
name: app-icon
description: >
  Make the icon for any app you build and wire it in: desktop apps (Electron, Tauri, PyInstaller, .NET,
  Qt), mobile apps (Flutter, React Native, Android, iOS), browser extensions, PWAs and launchers. Use
  every time you create or package an app, even when the user didn't mention an icon. For Cognitrix
  Apps (Publish App), the build-app skill covers it.
args:
  - name: app
    description: The app (name, what it does, where its code is)
    required: true
tags: [apps, icon, desktop, mobile, packaging, design]
category: development
version: "1.0.0"
author: cognitrix
safety:
  risk-level: low
---

# Give the app an icon

Every app you build gets its own icon: "$(arg app)". It is what people see in the Start menu, Dock,
taskbar, desktop shortcut, app launcher and window title bar, so never ship the framework's default
logo or leave it blank. Do this as part of building the app, without being asked.

## 1. Design it

- One bold, simple symbol of what the app does (a checkmark for a todo list, a waveform for an audio
  tool) on a full-bleed rounded tile in a colour that suits the app. Centred, about half the tile.
- No words. One or two letters at most. No thin lines or tiny details: it must read at 16 px.
- Square. Keep the master big: an SVG with `viewBox="0 0 1024 1024"`, or a 1024 x 1024 PNG.
- macOS icons usually leave a margin (the tile about 824 px inside the 1024 canvas); Windows and Linux
  icons fill the square.

## 2. Make the master file

Pick whichever you can do here:

- **Draw an SVG** (`icon.svg`) with `Write`, then turn it into a PNG: `rsvg-convert -w 1024 -h 1024
  icon.svg -o icon.png`, `inkscape icon.svg -w 1024 -o icon.png`, or `pip install cairosvg` and
  `cairosvg icon.svg -W 1024 -H 1024 -o icon.png`.
- **Draw the PNG with Python** (Pillow: `pip install pillow`): a rounded rectangle plus simple shapes.
- **Generate a picture** with `Generate Image` (`aspect_ratio: "1:1"`, a prompt like "flat app icon of
  a <symbol> on a <colour> rounded square, no text") and `save_to` a path inside the apps folder, e.g.
  `~/.cognitrix/apps/<name>/icon.png`; copy it into the project from there.

Look at the PNG (Show Screenshot, or open it) before going on. Fix it if it's blurry, cropped or busy.

## 3. Make the sizes each system needs

With Pillow, from `icon.png` (1024 x 1024):

```python
from PIL import Image
src = Image.open('icon.png').convert('RGBA')
src.save('icon.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])  # Windows
src.save('icon.icns')                                                                                    # macOS
for n in (16, 32, 48, 64, 128, 256, 512):
    src.resize((n, n), Image.LANCZOS).save(f'icon-{n}.png')                                          # Linux, web
```

On a Mac you can also use `iconutil -c icns icon.iconset`.

## 4. Wire it in

Use the framework's own setting, so installers, shortcuts and windows all show it:

- **Tauri**: `npx tauri icon icon.png` (writes `src-tauri/icons/`; `bundle.icon` already lists them).
- **Electron**: electron-builder `build.icon` (`icon.png` 512+, or `build/icon.ico` / `build/icon.icns`),
  and `new BrowserWindow({ icon })` for Linux windows.
- **PyInstaller**: `--icon icon.ico` (Windows) or `--icon icon.icns` (macOS); Tk/Qt windows also call
  `iconphoto` / `setWindowIcon`.
- **.NET / WinForms / WPF**: `<ApplicationIcon>icon.ico</ApplicationIcon>` in the .csproj.
- **Linux**: install `icon-256.png` (hicolor theme or next to the app) and set `Icon=` in the `.desktop` file.
- **Windows shortcut** you create yourself: `IconLocation` = the `.ico` (or the `.exe`).
- **Flutter**: `flutter_launcher_icons` (`image_path: icon.png`). **React Native / Android / iOS**: the
  launcher icon sets (mipmap-*, AppIcon.appiconset), e.g. with `npx @bam.tech/react-native-make` or
  Android Studio's Image Asset tool.
- **Browser extension**: `icons` in manifest.json (16, 32, 48, 128). **PWA / web**: `<link rel="icon">`
  plus `icons` in the web manifest (192 and 512).

## 5. Check and tell

Build or start the app once and check the icon shows (window, taskbar or launcher) where you can.
Keep the master (`icon.svg` / `icon.png`) in the project so later changes start from it, and mention
the icon in one line when you tell the user the app is ready.
