---
name: mouse-double-click
description: Perform a mouse double-click at specified screen coordinates
context: fork
argument-hint: <x> <y>
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

1. Run the double click script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/double_click.py "$ARGUMENTS"
   ```
2. Report success

## Notes

- Used to open files, folders, or trigger double-click actions
- Coordinates are pixels from top-left corner of screen
- Requires pyautogui: `pip install pyautogui`