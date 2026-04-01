---
name: hot-key
description: Press a combination of keys simultaneously (e.g., Ctrl+C, Alt+Tab)
context: fork
argument-hint: <key1> <key2> [key3...]
tags: [keyboard, hotkey, combo, shortcut, automation]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
safety:
  risk-level: medium
---

# Hot Key

Press multiple keys simultaneously (keyboard shortcut).

## Input Format

Arguments: `<key1> <key2> [key3...]`

Example: `ctrl c` (copy), `alt tab` (switch window), `ctrl shift escape`

## Steps

1. Run the combo script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/combo.py "$ARGUMENTS"
   ```
2. Report success

## Common Hot Keys

- `ctrl c` - Copy
- `ctrl v` - Paste
- `ctrl x` - Cut
- `ctrl z` - Undo
- `ctrl s` - Save
- `alt tab` - Switch window
- `ctrl shift escape` - Task manager
- `win d` - Show desktop

## Notes

- Keys are pressed simultaneously
- Requires pyautogui: `pip install pyautogui`