---
name: wikipedia
description: Search and retrieve information from Wikipedia
context: fork
argument-hint: <search_query> [basic|advanced]
tags: [wikipedia, encyclopedia, search]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [wikipedia]
safety:
  risk-level: low
---

# Wikipedia Search

Search Wikipedia and retrieve information about a topic.

## Input Format

Arguments: `<search_query> [basic|advanced]`

- `basic` (default): Returns a short summary
- `advanced`: Returns full page content

## Steps

1. Run the search script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/search.py "$ARGUMENTS"
   ```
2. Format and return search results

## Notes

- For ambiguous queries, returns disambiguation options
- Use basic mode for quick summaries, advanced for full content
- Returns up to 5000 characters for advanced mode