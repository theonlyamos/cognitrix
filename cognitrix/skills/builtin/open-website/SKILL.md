---
name: open-website
description: Open a URL in the default web browser
context: fork
argument-hint: <url>
tags: [web, browser, open]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
safety:
  risk-level: low
---

# Open Website

Open the specified URL in the default web browser.

## Steps

1. Run the open script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/open.py "$ARGUMENTS"
   ```
2. Report success or any errors

## Notes

- If URL doesn't have a protocol, prepends https:// automatically
- Works on Linux (xdg-open), macOS (open), Windows (start)
- This skill opens the browser but doesn't fetch page content