---
name: brave-search
description: Search the web using Brave Search API for up-to-date information
context: fork
argument-hint: <search_query>
tags: [search, web, brave]
category: web
version: "1.0.0"
author: cognitrix
allowed-tools: [bash]
dependencies:
  pip: [requests]
safety:
  risk-level: low
---

# Brave Search

Search the web using Brave Search API for current information.

## Steps

1. Ensure BRAVE_API_KEY environment variable is set
2. Run the search script:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/search.py "$ARGUMENTS"
   ```
3. Format and return search results

## Notes

- Requires Brave Search API key (free tier available)
- Set API key via: `export BRAVE_API_KEY=your-api-key`
- Returns up to 10 results by default
- Each result includes title, description, and URL