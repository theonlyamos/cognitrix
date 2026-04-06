---
name: brave-search
description: Search the web using Brave Search API for up-to-date information
context: fork
args:
  - name: search_query
    description: The search query to look up
    required: true
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
2. Run the search script with the search_query:
   ```bash
   python ${COGNITRIX_SKILL_DIR}/scripts/search.py "$(arg search_query)"
   ```
3. Format and return search results

## Notes

- Requires Brave Search API key (free tier available)
- On Windows, set API key via: `set BRAVE_API_KEY=your-api-key` (or use system environment variables)
- On Linux/Mac, set API key via: `export BRAVE_API_KEY=your-api-key`
- Returns up to 10 results by default
- Each result includes title, description, and URL