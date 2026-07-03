---
name: mouse-right-click
description: Perform a mouse right-click at specified screen coordinates
context: fork
args:
  - name: x
    description: X coordinate (pixels from left edge)
    required: true
  - name: y
    description: Y coordinate (pixels from top edge)
    required: true
tags: [mouse, right-click, context-menu, automation, ui]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
safety:
  risk-level: medium
---

# Mouse Right Click

Perform a right mouse button click at specific screen coordinates.

## Input Format

Arguments: `<x> <y>`

Example: `100 200` (right-click at x=100, y=200)

## Steps

1. Run the right click script with x and y coordinates:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/right_click.py "$(arg x)" "$(arg y)"
   ```
2. Report success

## Notes

- Opens context menu at the specified position
- Coordinates are pixels from top-left corner of screen
- Requires pyautogui: `pip install pyautogui`