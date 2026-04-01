---
name: mouse-click
description: Perform a mouse left-click at specified screen coordinates
context: fork
argument-hint: <x> <y>
tags: [mouse, click, automation, ui]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
---

# Mouse Click

Perform a left mouse button click at specific screen coordinates.

## Input Format

Arguments: `<x> <y>`

Example: `100 200` (click at x=100, y=200)

## Steps

1. Run the click script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/click.py "$ARGUMENTS"
   ```
2. Report success

## Notes

- Coordinates are pixels from top-left corner of screen
- To find coordinates, use screen resolution or a coordinate picker tool
- Requires pyautogui: `pip install pyautogui`