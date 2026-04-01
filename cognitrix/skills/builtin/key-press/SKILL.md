---
name: key-press
description: Press a single key on the keyboard
context: fork
args:
  - name: key_name
    description: The key to press (e.g., enter, esc, a, f1, ctrl)
    required: true
tags: [keyboard, key, press, automation]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
safety:
  risk-level: medium
---

# Key Press

Press a single key on the keyboard.

## Input Format 

Arguments: `<key_name>`

Example: `enter`, `esc`, `a`, `f1`, `ctrl`

## Steps

1. Run the press script with the key_name:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/press.py "$(arg key_name)"
   ```
2. Report success

## Notes

- Common keys: enter, esc, tab, space, backspace, delete
- Letter keys: a-z
- Number keys: 0-9
- Function keys: f1-f12
- Modifier keys: ctrl, alt, shift, cmd, win
- Requires pyautogui: `pip install pyautogui`