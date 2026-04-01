---
name: key-press
description: Press a single key on the keyboard
context: fork
argument-hint: <key_name>
tags: [keyboard, key, press, automation]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
---

# Key Press

Press a single key on the keyboard.

## Input Format 

Arguments: `<key_name>`

Example: `enter`, `esc`, `a`, `f1`, `ctrl`

## Steps

1. Run the press script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/press.py "$ARGUMENTS"
   ```
2. Report success

## Notes

- Common keys: enter, esc, tab, space, backspace, delete
- Letter keys: a-z
- Number keys: 0-9
- Function keys: f1-f12
- Modifier keys: ctrl, alt, shift, cmd, win
- Requires pyautogui: `pip install pyautogui`