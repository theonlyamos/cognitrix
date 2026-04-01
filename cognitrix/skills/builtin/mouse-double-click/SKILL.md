---
name: mouse-double-click
description: Perform a mouse double-click at specified screen coordinates
context: fork
args:
  - name: x
    description: X coordinate (pixels from left edge)
    required: true
  - name: y
    description: Y coordinate (pixels from top edge)
    required: true
tags: [mouse, double-click, automation, ui]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
safety:
  risk-level: medium
---

# Mouse Double Click

Perform a double left mouse button click at specific screen coordinates.

## Input Format

Arguments: `<x> <y>`

Example: `100 200` (double-click at x=100, y=200)

## Steps

1. Run the double click script with x and y coordinates:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/double_click.py "$(arg x)" "$(arg y)"
   ```
2. Report success

## Notes

- Used to open files, folders, or trigger double-click actions
- Coordinates are pixels from top-left corner of screen
- Requires pyautogui: `pip install pyautogui`