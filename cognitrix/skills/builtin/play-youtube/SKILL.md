---
name: play-youtube
description: Search YouTube for a topic and open the first video result in browser
context: fork
argument-hint: <search_topic>
tags: [youtube, video, search]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [requests]
---

# Play YouTube

Search YouTube for a topic and open the first video result.

## Steps

1. Run the search script to get video URL:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/search.py "$ARGUMENTS"
   ```
2. If a video URL is found, open it in the browser:
   - Linux: `xdg-open "<url>"`
   - macOS: `open "<url>"`
   - Windows: `start "" "<url>"`
3. Return the video URL that was opened

## Notes

- Uses YouTube's search results page to find the first video
- If no video is found, return an error message
- Requires requests library for HTTP requests