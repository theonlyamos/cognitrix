---
name: mouse-click
description: Perform a mouse left-click at specified screen coordinates
context: fork
args:
  - name: x
    description: X coordinate (pixels from left edge)
    required: true
  - name: y
    description: Y coordinate (pixels from top edge)
    required: true
tags: [mouse, click, automation, ui]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
safety:
  risk-level: medium
---

# Mouse Click

Perform a left mouse button click at specific screen coordinates.

## Input Format

Arguments: `<x> <y>`

Example: `100 200` (click at x=100, y=200)

## Steps

1. Run the click script with x and y coordinates:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/click.py "$(arg x)" "$(arg y)"
   ```
2. Report success

## Notes

- Coordinates are pixels from top-left corner of screen
- To find coordinates, use screen resolution or a coordinate picker tool
- Requires pyautogui: `pip install pyautogui`