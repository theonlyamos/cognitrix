---
name: text-input
description: Type text into the currently focused application
context: fork
argument-hint: <text_to_type>
tags: [keyboard, type, input, automation, ui]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
---

# Text Input

Type text into the currently focused application.

## Input Format

Arguments: `<text_to_type>`

Example: `Hello World` or `filename.txt`

## Steps

1. Run the type script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/type.py "$ARGUMENTS"
   ```
2. Report success

## Notes

- Text is typed into whatever application is currently focused
- Use `key-press` skill to press Enter after typing
- Use `hot-key` skill for keyboard shortcuts
- Requires pyautogui: `pip install pyautogui`