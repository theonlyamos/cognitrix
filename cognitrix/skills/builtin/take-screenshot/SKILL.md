---
name: take-screenshot
description: Take a screenshot of the screen and save to a file
context: fork
argument-hint: "<output_path>"
tags: [screenshot, capture, screen]
category: system
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [pyautogui]
safety:
  risk-level: medium
---

# Take Screenshot

Take a screenshot of the screen and save it to a file.

## Input Format

Optional argument: `[output_path]`
- If omitted, saves as `screenshot.png` in current directory

## Steps

1. Run the capture script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/capture.py "$ARGUMENTS"
   ```
2. Report the file path where screenshot was saved

## Notes

- Requires pyautogui library: `pip install pyautogui`
- On Linux, may need `scrot` or `gnome-screenshot` installed
- On macOS, requires permission for screen recording
- On Windows, may require admin privileges for full screen capture